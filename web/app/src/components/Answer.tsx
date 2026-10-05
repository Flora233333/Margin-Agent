/*
 * 回答：写在纸面上的正文（不加框）。
 *   结论行  大号的答案 + 一行说明（简洁风的 .verdict）；
 *   依据    每条通过校验的引用一行，末尾是可点的编号 [n]，点了跳到右侧对应的来源旁注；
 *   账目行  模型、步数、引用数。
 * 模型交答案用的是 finalize 工具（answers 数组），不写一段自然语言回答，所以“正文”就是依据列表。
 */

import type { AnswerFormat } from '../api'
import type { Citation } from '../citations'
import type { Attempt } from '../timeline'

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
  citations: Citation[]
  linked: number | null
  onCiteHover: (no: number | null) => void
  onCiteClick: (no: number) => void
}

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
  const figure = submitted.join('、')
  const unit = format === 'pct' && !figure.endsWith('%') ? '%' : ''
  // 选择题：结论行显示字母，说明里带上选项原文
  const chosen = options ? submitted.map((key) => options[key]).filter(Boolean).join('；') : ''
  return (
    <div className="verdict">
      <div className="verdict-main">
        <span className="verdict-figure">
          {figure}
          {unit && <span className="verdict-unit">{unit}</span>}
        </span>
        <span className="verdict-label">{chosen || `${FORMAT_LABEL[format]}答案`}</span>
      </div>
    </div>
  )
}

export function Answer(props: Props) {
  const { attempt, citations, model, linked, onCiteHover, onCiteClick } = props
  return (
    <section className="answer">
      <Verdict attempt={attempt} format={props.format} options={props.options} />
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
      </div>
    </section>
  )
}
