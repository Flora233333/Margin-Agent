/*
 * 撰写的回答（纯文本，src/margin/compose.py）切成页面要显示的段落：
 *   - 空行（或换行）分段；以“口径说明”开头的一段单独标出来，小号灰字（PLAN §5.8 ③ 的输出约定）；
 *   - 段落里的 [n] 切出来，变成可点的引用编号。
 * 只认右侧真有卡片的编号：逐字输出时的正文还没经过后端校验，可能带着之后会被删掉的 [n]，先不显示。
 */

export type Part = string | number // 文字，或者引用编号

export interface Paragraph {
  caveat: boolean
  parts: Part[]
}

export function paragraphsOf(text: string, valid: Set<number>): Paragraph[] {
  return text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => ({
      caveat: line.startsWith('口径说明'),
      // split 的正则带括号时，括号里匹配到的编号也会留在结果里：奇数位是编号，偶数位是文字
      parts: line.split(/\[(\d+)\]/).flatMap((piece, i): Part[] => {
        if (i % 2 === 0) {
          return piece ? [piece] : []
        }
        return valid.has(Number(piece)) ? [Number(piece)] : []
      }),
    }))
}
