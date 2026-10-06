/*
 * 回答：写在纸面上的正文（不加框）。
 *   结论行  大号的答案 + 一行说明（简洁风的 .verdict）；
 *   依据    每条通过校验的引用一行，末尾是可点的编号 [n]，点了跳到右侧对应的来源旁注；
 *   账目行  模型、步数、引用数；“复制”按钮（结论 + 依据，纯文本）。
 * 结论是单个数字时，出现时从 0 滚动到这个数（CountUp，设计稿的 countUp）。
 * 重新生成过的题，更早的执行只显示结论行（citations 传 null）：引用编号和右侧旁注只属于最近一次执行。
 * 模型交答案用的是 finalize 工具（answers 数组），不写一段自然语言回答，所以“正文”就是依据列表。
 */

import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { AnswerFormat } from '../api'
import type { Citation } from '../citations'
import type { Attempt } from '../timeline'
import { Icon } from './Icons'

const FORMAT_LABEL: Record<AnswerFormat, string> = {
  num: '数值', pct: '百分比', tf: '判断', mcq: '单选', multi: '多选', date: '日期', rank: '排序', text: '文本',
}

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

function Verdict({ attempt, format, options }: Pick<Props, 'attempt' | 'format' | 'options'>) {
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
  // 选择题：结论行显示字母，说明里带上选项原文
  const chosen = options ? submitted.map((key) => options[key]).filter(Boolean).join('；') : ''
  return (
    <div className="verdict">
      <div className="verdict-main">
        <span className="verdict-figure">
          {NUMBER.test(figure) ? <CountUp value={figure} /> : figure}
          {unit && <span className="verdict-unit">{unit}</span>}
        </span>
        <span className="verdict-label">{chosen || `${FORMAT_LABEL[format]}答案`}</span>
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

/** 复制的内容：结论一行，依据每条一行（[n] 引文（文档）） */
function plainText(attempt: Attempt, format: AnswerFormat, citations: Citation[]): string {
  const lines = [figureOf(attempt, format) ?? '没有答案']
  for (const c of citations) {
    lines.push(`[${c.no}] ${c.match}（${c.docId}）`)
  }
  return lines.join('\n')
}

export function Answer(props: Props) {
  const { attempt, citations, model, linked, onCiteHover, onCiteClick } = props
  const verdict = <Verdict attempt={attempt} format={props.format} options={props.options} />
  if (citations === null) {
    return <section className="answer is-earlier">{verdict}</section>
  }
  return (
    <section className="answer">
      {verdict}
      {citations.length > 0 && (
        <ol className="evidence">
          {citations.map((c) => (
            <li key={c.no}>
              {c.match}
              <a
                className={linked === c.no ? 'cite is-linked' : 'cite'}
                href={`#src-${c.no}`}
                data-src={c.no}
                onMouseEnter={() => onCiteHover(c.no)}
                onMouseLeave={() => onCiteHover(null)}
                onClick={(e) => {
                  e.preventDefault()
                  onCiteClick(c.no)
                }}
              >
                {c.no}
              </a>
            </li>
          ))}
        </ol>
      )}
      <div className="answer-foot">
        <span className="ledger">
          <span>{model}</span>
          <span>{attempt.steps.length} 步</span>
          <span>{citations.length} 处引用</span>
        </span>
        <CopyButton text={plainText(attempt, props.format, citations)} />
      </div>
    </section>
  )
}
