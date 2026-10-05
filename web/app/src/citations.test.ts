import { expect, test } from 'vitest'
import { citationsOf } from './citations'
import type { StepEvent } from './timeline'

function step(step_no: number, tool_name: string, data: Record<string, unknown>, ok = true): StepEvent {
  return { attempt_id: 1, step_no, tool_name, arguments: '{}', result: ok ? { ok, data } : { ok, error: 'x' }, reasoning: null }
}

const read = step(0, 'read_section', {
  doc_id: 'text01', block_id: 'b1', text: '报告期内，公司实现营业收入120.5亿元，同比增长12.4%。',
})
const cite = (n: number, quote: string, grounded = true) =>
  step(n, 'cite', { doc_id: 'text01', block_id: 'b1', grounded, matched_text: quote })

test('只有通过校验的引用才编号，没在原文里找到的不显示成来源', () => {
  const citations = citationsOf([read, cite(1, '编造的句子', false), cite(2, '实现营业收入120.5亿元')])
  expect(citations.map((c) => [c.no, c.stepNo])).toEqual([[1, 2]])
})

test('引文在读过的原文里：旁注带上前后文', () => {
  const [c] = citationsOf([read, cite(1, '实现营业收入120.5亿元')])
  expect([c.before, c.match, c.after]).toEqual(['报告期内，公司', '实现营业收入120.5亿元', '，同比增长12.4%。'])
})

test('同一处原文引用两次只编一个号，编号不跳', () => {
  const citations = citationsOf([
    read, cite(1, '实现营业收入120.5亿元'), cite(2, '实现营业收入120.5亿元'), cite(3, '同比增长12.4%'),
  ])
  expect(citations.map((c) => c.no)).toEqual([1, 2])
  expect(citations[1].match).toBe('同比增长12.4%')
})
