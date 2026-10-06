import { expect, test } from 'vitest'
import { citationsOf, searchedDocsOf } from './citations'
import type { StepEvent } from './timeline'

function step(step_no: number, tool_name: string, data: Record<string, unknown>, citation_no: number | null = null): StepEvent {
  return { attempt_id: 1, step_no, tool_name, arguments: '{}', result: { ok: true, data }, reasoning: null, citation_no }
}

const read = step(0, 'read_section', {
  doc_id: 'text01', block_id: 'b1', text: '报告期内，公司实现营业收入120.5亿元，同比增长12.4%。',
})
// citation_no 是后端随 step 事件下发的编号；null 表示没通过校验，或重复引用了同一处原文
const cite = (n: number, quote: string, no: number | null) =>
  step(n, 'cite', { doc_id: 'text01', block_id: 'b1', grounded: true, matched_text: quote }, no)

test('只显示后端编了号的引用，编号用后端给的，不自己重新数', () => {
  const citations = citationsOf([
    read, cite(1, '实现营业收入120.5亿元', 1), cite(2, '实现营业收入120.5亿元', null), cite(3, '同比增长12.4%', 2),
  ])
  expect(citations.map((c) => [c.no, c.stepNo, c.match])).toEqual([
    [1, 1, '实现营业收入120.5亿元'], [2, 3, '同比增长12.4%']])
})

test('引文在读过的原文里：旁注带上前后文', () => {
  const [c] = citationsOf([read, cite(1, '实现营业收入120.5亿元', 1)])
  expect([c.before, c.match, c.after]).toEqual(['报告期内，公司', '实现营业收入120.5亿元', '，同比增长12.4%。'])
})

test('检索命中的其他文档：去掉已引用的文档，多次命中合并成一条、取最好的排名，并数出读过几块', () => {
  const search = (n: number, hits: [string, number][]) =>
    step(n, 'search_docs', { results: hits.map(([doc_id, rank]) => ({ doc_id, rank })) })
  const steps = [
    search(0, [['text01', 1], ['text02', 2], ['text03', 3]]),
    search(1, [['text03', 1], ['text02', 4]]),
    read, // text01 的 b1
    step(2, 'read_section', { doc_id: 'text03', block_id: 'b7', text: '……' }),
    step(3, 'read_section', { doc_id: 'text03', block_id: 'b7', text: '……' }), // 同一块读两次只算一块
    cite(4, '实现营业收入120.5亿元', 1),
  ]
  const docs = searchedDocsOf(steps, citationsOf(steps))
  expect(docs).toEqual([
    { docId: 'text02', rank: 2, read: 0 },
    { docId: 'text03', rank: 1, read: 1 },
  ])
})

test('原文里的 <sub> 标签和图片链接不直接显示在旁注里', () => {
  const raw = step(0, 'read_section', {
    doc_id: 'cscec', block_id: 'b4', text: '新签合同额45,027 <sub>亿元</sub>同比增长 4.1%\n![](images/dfeb0e.jpg)\n## 二、经营情况',
  })
  const [c] = citationsOf([raw, step(1, 'cite', { doc_id: 'cscec', block_id: 'b4', grounded: true, matched_text: '45,027 <sub>亿元</sub>' }, 1)])
  expect([c.before, c.match, c.after]).toEqual(['新签合同额', '45,027 亿元', '同比增长 4.1%\n〔图片〕\n## 二、经营情况'])
})
