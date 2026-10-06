import { expect, test } from 'vitest'
import { paragraphsOf } from './answerText'

test('回答按段落切开，[n] 变成引用编号，口径说明单独标出', () => {
  const text = '营业收入为1204.63亿元[1]。\n\n合并利润表为12,046,275.40万元[1][2]。\n\n口径说明：取“营业收入”行[2]。'
  expect(paragraphsOf(text, new Set([1, 2]))).toEqual([
    { caveat: false, parts: ['营业收入为1204.63亿元', 1, '。'] },
    { caveat: false, parts: ['合并利润表为12,046,275.40万元', 1, 2, '。'] },
    { caveat: true, parts: ['口径说明：取“营业收入”行', 2, '。'] },
  ])
})

test('逐字输出时还没校验的编号（右侧没有这张卡片）先不显示，避免点了没反应', () => {
  expect(paragraphsOf('同比增长12.4%[2][5]。', new Set([1, 2]))).toEqual([
    { caveat: false, parts: ['同比增长12.4%', 2, '。'] },
  ])
})
