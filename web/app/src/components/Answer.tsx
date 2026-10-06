/*
 * 回答：写在纸面上的正文（不加框），按设计稿的结构（PLAN §5.8 前端）：
 *   结论行  数值 / 百分比 / 日期 / 判断等：大号的答案 + 一行说明（理解题目给的 label）；
 *           开放问题（text）没有一个“数”可以放大，不显示结论行，正文第一句就是结论；
 *   正文    撰写的回答（compose.py），事实后面跟着可点的 [n]，点了跳到右侧对应的来源旁注；
 *           以“口径说明”开头的一段小号灰字；
 *   账目行  模型、步数、引用数；“复制”按钮。
 * 撰写失败（或 M2.5 之前的旧题，没有撰写的回答）时退回原来的样子：依据列表，每条引文一行。
 * 执行中撰写的正文逐字出现；结束后换成后端校验过的文字（timeline.ts 规则 3）。
 * 结论是单个数字时，出现时从 0 滚动到这个数（CountUp，设计稿的 countUp）。
 * 重新生成过的题，更早的执行只显示结论（citations 传 null）：引用编号和右侧旁注只属于最近一次执行。
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { type AnswerFormat, FORMAT_LABEL } from '../api'
import { paragraphsOf } from '../answerText'
import type { Citation } from '../citations'
import type { Attempt } from '../timeline'
import { Icon } from './Icons'

// 模型停下来却没有交答案的原因（harness/loop.py 的停止条件）
const VIOLATION_LABEL: Record<string, string> = {
  max_turns: '工具调用次数用完，没有提交答案',
  provider_call_limit: '模型调用次数用完，没有提交答案',
  answer_format_retry_exhausted: '答案格式多次不符合要求',
  duplicate_no_progress: '重复相同的调用、没有进展，已停止',
  repeated_tool_error: '工具连续出错，已停止',
  multiple_tool_calls: '模型一次调用了多个工具（不符合协议），已停止',
  provider_protocol_retry_exhausted: '模型多次没有按协议调用工具，已停止',
}

interface Props {
  attempt: Attempt
  format: AnswerFormat
  label: string | null // 结论旁边的说明，例如“广晟控股 · 2022 年营业收入（亿元）”
  options: Record<string, string> | null
  model: string
  citations: Citation[] | null
  linked: number | null
  onCiteHover: (no: number | null) => void
  onCiteClick: (no: number) => void
}

/** 结论行的文字：判断题换回“正确 / 错误”，其余照原样（多个答案用顿号连接）；没有提交答案时为 null */
function figureOf(attempt: Attempt, format: AnswerFormat): string | null {
  const submitted = (attempt.final?.submitted as string[] | undefined) ?? []
  if (attempt.final?.name === 'escalate' || submitted.length === 0) {
    return null
  }
  // 判断题：harness 把答案统一成 A（正确）/ B（错误）（harness/answers.py），显示时换回文字
  return format === 'tf' ? (submitted[0] === 'A' ? '正确' : '错误') : submitted.join('、')
}

/**
 * 数字从 0 滚动到目标值，先快后慢，0.8 秒；小数位数和目标值相同。
 * 每一帧直接改文字（textContent），不经过 React 的 state，免得一秒重新渲染 60 次。
 * 用 useLayoutEffect 在浏览器画出来之前就把文字设成 0，否则会先闪一下最终的数。
 */
function CountUp({ value }: { value: string }) {
  const el = useRef<HTMLSpanElement>(null)
  useLayoutEffect(() => {
    const target = Number(value)
    const decimals = (value.split('.')[1] ?? '').length
    const start = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 800)
      el.current!.textContent = (target * (1 - (1 - t) ** 3)).toFixed(decimals)
      if (t < 1) {
        frame = requestAnimationFrame(tick)
      }
    }
    tick(start)
    return () => cancelAnimationFrame(frame)
  }, [value])
  return <span ref={el}>{value}</span>
}

const NUMBER = /^-?\d+(\.\d+)?$/

function Verdict({ attempt, format, label, options }: Pick<Props, 'attempt' | 'format' | 'label' | 'options'>) {
  const final = attempt.final
  if (final?.name === 'escalate') {
    return (
      <div className="verdict is-empty">
        <div className="verdict-main">
          <span className="verdict-figure">未作答</span>
          <span className="verdict-label">{`${final.reason_code}：${final.detail}`}</span>
        </div>
      </div>
    )
  }
  const submitted = (final?.submitted as string[] | undefined) ?? []
  if (submitted.length === 0) {
    return (
      <div className="verdict is-empty">
        <div className="verdict-main">
          <span className="verdict-figure">没有答案</span>
          <span className="verdict-label">{VIOLATION_LABEL[attempt.violation ?? ''] ?? attempt.violation}</span>
        </div>
      </div>
    )
  }
  const figure = figureOf(attempt, format)!
  const unit = format === 'pct' && !figure.endsWith('%') ? '%' : ''
  // 选择题（评测回放的题）：结论行显示字母，说明里带上选项原文
  const chosen = options ? submitted.map((key) => options[key]).filter(Boolean).join('；') : ''
  return (
    <div className="verdict">
      <div className="verdict-main">
        <span className="verdict-figure">
          {NUMBER.test(figure) ? <CountUp value={figure} /> : figure}
          {unit && <span className="verdict-unit">{unit}</span>}
        </span>
        <span className="verdict-label">{chosen || label || `${FORMAT_LABEL[format]}答案`}</span>
      </div>
    </div>
  )
}

/** 复制按钮：成功后图标换成 ✓、文字变“已复制”，1.5 秒后恢复 */
function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  useEffect(() => {
    if (state === 'idle') {
      return
    }
    const timer = setTimeout(() => setState('idle'), 1500)
    return () => clearTimeout(timer)
  }, [state])
  // 剪贴板是浏览器接口，可能因为没有权限被拒绝（例如页面不在前台），这时告诉用户没复制成功
  const copy = () => navigator.clipboard.writeText(text).then(() => setState('copied'), () => setState('failed'))
  return (
    <button className={state === 'copied' ? 'icon-btn copy-btn is-swapped' : 'icon-btn copy-btn'} type="button" onClick={() => void copy()}>
      <span className="swap">
        <Icon name="copy" />
        <Icon name="ok" />
      </span>
      <span className="btn-label">{{ idle: '复制', copied: '已复制', failed: '复制失败' }[state]}</span>
    </button>
  )
}

/** 复制的内容：回答正文（没有时是结论），后面列出每条引用（[n] 引文（文档）） */
function plainText(attempt: Attempt, format: AnswerFormat, citations: Citation[]): string {
  const lines = [attempt.written?.text ?? figureOf(attempt, format) ?? '没有答案', '']
  for (const c of citations) {
    lines.push(`[${c.no}] ${c.match}（${c.docId}）`)
  }
  return lines.join('\n').trim()
}

type CiteProps = Pick<Props, 'linked' | 'onCiteHover' | 'onCiteClick'> & { no: number }

function Cite({ no, linked, onCiteHover, onCiteClick }: CiteProps) {
  return (
    <a
      className={linked === no ? 'cite is-linked' : 'cite'}
      href={`#src-${no}`}
      data-src={no}
      onMouseEnter={() => onCiteHover(no)}
      onMouseLeave={() => onCiteHover(null)}
      onClick={(e) => {
        e.preventDefault()
        onCiteClick(no)
      }}
    >
      {no}
    </a>
  )
}

export function Answer(props: Props) {
  const { attempt, format, citations, model } = props
  const text = attempt.written?.text ?? attempt.answer
  // 开放问题只要有正文就不显示结论行；没有正文（撰写失败、旧题）时仍用结论行显示短答案
  const showVerdict = format !== 'text' || !text || attempt.final?.name === 'escalate' || !attempt.final?.submitted
  const verdict = showVerdict && <Verdict attempt={attempt} format={format} label={props.label} options={props.options} />
  if (citations === null) {
    return <section className="answer is-earlier">{verdict || <p>{text.replace(/\[\d+\]/g, '').split('\n')[0]}</p>}</section>
  }
  const writing = attempt.status === 'running'
  const unverified = attempt.written?.unverified ?? []
  const valid = new Set(citations.map((c) => c.no))
  const cite = { linked: props.linked, onCiteHover: props.onCiteHover, onCiteClick: props.onCiteClick }
  return (
    <section className={writing ? 'answer is-writing' : 'answer'}>
      {!writing && verdict}
      {attempt.final?.uncited === true && (
        <p className="answer-note">
          <Icon name="alert" />
          本回答没有引用原文
        </p>
      )}
      {text
        ? paragraphsOf(text, valid).map((p, i) => (
            <p key={i} className={p.caveat ? 'caveat' : undefined}>
              {p.parts.map((part, j) =>
                typeof part === 'number' ? <Cite key={j} no={part} {...cite} /> : part,
              )}
            </p>
          ))
        : citations.length > 0 && (
            <ol className="evidence">
              {citations.map((c) => (
                <li key={c.no}>
                  {c.match}
                  <Cite no={c.no} {...cite} />
                </li>
              ))}
            </ol>
          )}
      {attempt.written?.error && <p className="answer-note">回答生成失败，上面是提交的答案和依据，可以重新生成。</p>}
      {unverified.length > 0 && (
        <p className="answer-note">有 {unverified.length} 个数字没能在引用的原文里核对到：{unverified.join('、')}</p>
      )}
      {!writing && (
        <div className="answer-foot">
          <span className="ledger">
            <span>{model}</span>
            <span>{attempt.steps.length} 步</span>
            <span>{citations.length} 处引用</span>
          </span>
          <CopyButton text={plainText(attempt, format, citations)} />
        </div>
      )}
    </section>
  )
}
