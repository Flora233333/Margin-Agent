/*
 * 跨组件共用的出场时间（毫秒）。交卷后：过程区停留一下再收起（Work.tsx），
 * 收起之后回答才按“结论 → 正文”出场（Answer.tsx）。
 */

export const FOLD_DELAY_MS = 700 // 交卷后过程区停留多久再收起（同设计稿 demo.js 的 finishWork）
export const FOLD_MS = 500 // 收起的过渡时长（base.css 的 .reveal）
