/*
 * 引用：从一次执行的步骤里取出带编号的 cite 调用，给回答里的引用编号和右侧的来源旁注用。
 *
 * 编号由后端编好、随 step 事件下发（citation_no，规则见 src/margin/citations.py）：
 * 只有通过校验的引用有编号，同一处原文引用两次只有第一次有编号。前端只按编号显示，不自己编号——
 * 撰写回答（M2.5-4）写出的 [n] 和这里的卡片必须是同一套编号，规则只能有一份。
 *
 * cite 工具的结果（harness/tools/reading.py）：matched_text 是在原文里实际匹配到的那段。
 *
 * 旁注里想多显示一点上下文：模型引用之前一定读过这一块（read_section 或 find_in_block，否则校验不通过），
 * 就在那些步骤的结果里找到引文所在位置，前后各取一段。
 *
 * 另外整理“检索命中的其他文档”（searchedDocsOf）：search_docs 命中、但没有被引用的文档。
 * 模型没有引用任何原文时（例如 run 31 直接算完就交答案），右栏至少能看到它查过、读过哪些文档。
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
    if (step.citation_no === null) {
      continue
    }
    const data: Json = step.result.data as Json
    citations.push({
      no: step.citation_no,
      stepNo: step.step_no,
      docId: data.doc_id,
      blockId: data.block_id,
      ...withContext(textsOf(steps, data.doc_id, data.block_id), data.matched_text),
    })
  }
  return citations
}

export interface SearchedDoc {
  docId: string
  rank: number // 在各次 search_docs 里最好的排名（1 最相关）
  read: number // 读过其中几块（read_section 的不同 block_id 数）
}

/** search_docs 命中、但没有被引用的文档，按第一次命中的先后排列 */
export function searchedDocsOf(steps: StepEvent[], citations: Citation[]): SearchedDoc[] {
  const docs = new Map<string, SearchedDoc>()
  for (const step of steps) {
    const data: Json = (step.result.data as Json) ?? {}
    if (step.tool_name !== 'search_docs') {
      continue
    }
    for (const hit of data.results ?? []) {
      const doc = docs.get(hit.doc_id)
      if (doc) {
        doc.rank = Math.min(doc.rank, hit.rank)
      } else {
        docs.set(hit.doc_id, { docId: hit.doc_id, rank: hit.rank, read: 0 })
      }
    }
  }
  const blocks = new Set<string>()
  for (const step of steps) {
    const data: Json = (step.result.data as Json) ?? {}
    const doc = docs.get(data.doc_id)
    const key = `${data.doc_id}/${data.block_id}`
    if (step.tool_name === 'read_section' && doc && !blocks.has(key)) {
      blocks.add(key)
      doc.read += 1
    }
  }
  const cited = new Set(citations.map((c) => c.docId))
  return [...docs.values()].filter((doc) => !cited.has(doc.docId))
}
