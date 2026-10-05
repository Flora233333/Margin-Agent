/*
 * 引用：从一次执行的步骤里取出通过校验的 cite 调用，编号 [1] [2]……，给回答里的引用编号和右侧的来源旁注用。
 *
 * cite 工具的结果（harness/tools/reading.py）：grounded=true 表示引文确实出自模型读过的原文，
 * matched_text 是在原文里实际匹配到的那段。没通过校验的引用不编号、不显示成来源。
 *
 * 旁注里想多显示一点上下文：模型引用之前一定读过这一块（read_section 或 find_in_block，否则校验不通过），
 * 就在那些步骤的结果里找到引文所在位置，前后各取一段。
 */

import type { StepEvent } from './timeline'

const CONTEXT_CHARS = 60

export interface Citation {
  no: number
  stepNo: number
  docId: string
  blockId: string
  before: string // 引文前面的原文（可能为空）
  match: string
  after: string
}

type Json = Record<string, any>

/** 这次执行里读到过的、属于这一块的原文片段 */
function textsOf(steps: StepEvent[], docId: string, blockId: string): string[] {
  const texts: string[] = []
  for (const step of steps) {
    const data: Json = (step.result.data as Json) ?? {}
    if (data.doc_id !== docId || data.block_id !== blockId) {
      continue
    }
    if (step.tool_name === 'read_section') {
      texts.push(data.text)
    } else if (step.tool_name === 'find_in_block') {
      texts.push(...data.matches.map((m: Json) => m.text))
    }
  }
  return texts
}

function withContext(texts: string[], match: string): Pick<Citation, 'before' | 'match' | 'after'> {
  for (const text of texts) {
    const at = text.indexOf(match)
    if (at >= 0) {
      const start = Math.max(0, at - CONTEXT_CHARS)
      const end = Math.min(text.length, at + match.length + CONTEXT_CHARS)
      return {
        before: (start > 0 ? '…' : '') + text.slice(start, at),
        match,
        after: text.slice(at + match.length, end) + (end < text.length ? '…' : ''),
      }
    }
  }
  return { before: '', match, after: '' }
}

export function citationsOf(steps: StepEvent[]): Citation[] {
  const citations: Citation[] = []
  for (const step of steps) {
    const data: Json = (step.result.data as Json) ?? {}
    if (step.tool_name !== 'cite' || !data.grounded) {
      continue
    }
    // 同一处原文引用了两次（模型有时会重复确认），只编一个号
    const seen = citations.some(
      (c) => c.docId === data.doc_id && c.blockId === data.block_id && c.match === data.matched_text)
    if (seen) {
      continue
    }
    citations.push({
      no: citations.length + 1,
      stepNo: step.step_no,
      docId: data.doc_id,
      blockId: data.block_id,
      ...withContext(textsOf(steps, data.doc_id, data.block_id), data.matched_text),
    })
  }
  return citations
}
