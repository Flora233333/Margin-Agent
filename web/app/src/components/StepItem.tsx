/*
 * 时间线里的一条：思考（灰色小点 + 一段文字）或一次工具调用（图标 + 标题 + 一行结果）。
 * 每种工具显示什么，按 harness 的工具结果字段来（src/margin/harness/tools/）：
 * 结果统一是 {ok: true, data: {...}} 或 {ok: false, error: 错误码, hint: 怎么改}。
 */

import type { ReactNode } from 'react'
import type { StepEvent } from '../timeline'
import { Icon } from './Icons'

// 工具结果的字段因工具而异，这里不给每种工具单独写类型，按工具名直接取字段
type Json = Record<string, any>

interface Card {
  icon: ReactNode
  title: ReactNode
  detail?: ReactNode
  body?: ReactNode // 标题下方的额外内容：公式、笔记
}

function q(text: unknown) {
  return <q>{String(text)}</q>
}

function joinQuoted(items: unknown[]) {
  return items.map((item, i) => (
    <span key={i}>
      {i > 0 && '、'}
      {q(item)}
    </span>
  ))
}

/** 模型给的参数是 JSON 字符串；格式坏了（工具会返回错误让它重试）就当成没有参数 */
function parseArgs(raw: string): Json {
  try {
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

const CHECK = (
  <svg className="check-draw" viewBox="0 0 20 20">
    <circle cx="10" cy="10" r="8" pathLength={1} />
    <path d="M6.3 10.3l2.6 2.6 4.9-5.3" pathLength={1} />
  </svg>
)

function describe(step: StepEvent): Card {
  const args = parseArgs(step.arguments)
  const data: Json = (step.result.data as Json) ?? {}
  switch (step.tool_name) {
    case 'search_docs': {
      const results: Json[] = data.results ?? []
      return {
        icon: <Icon name="search" />,
        title: <>检索文档 {q(args.query)}</>,
        detail: results.length > 0 ? `命中 ${results.length} 篇，最相关：${results[0].doc_id}` : '没有命中',
      }
    }
    case 'search_in_document': {
      const queries: unknown[] = args.queries ?? [args.query]
      const total = (data.query_results ?? [data]).reduce(
        (n: number, r: Json) => n + (r.results?.length ?? 0), 0)
      return {
        icon: <Icon name="search" />,
        title: <>在 {args.doc_id} 中检索 {joinQuoted(queries)}</>,
        detail: `${total} 处结果`,
      }
    }
    case 'read_section':
      return {
        icon: <Icon name="read" />,
        title: `阅读 ${args.doc_id} · ${args.block_id}`,
        detail: data.block_lines
          ? `第 ${data.row_offset + 1}–${data.returned_line_end_exclusive} 行，共 ${data.block_lines} 行`
          : undefined,
      }
    case 'find_in_block':
      return {
        icon: <Icon name="search" />,
        title: <>在 {args.block_id} 中查找 {joinQuoted(args.keywords ?? [])}</>,
        detail: `找到 ${data.match_count_total ?? 0} 处`,
      }
    case 'cite':
      return {
        icon: <Icon name="cite" />,
        title: <>引用 {q(args.quote)}</>,
        detail: data.grounded ? (data.level === 'exact' ? '原文逐字匹配' : '原文匹配（忽略空白和标点）') : '没有在原文中找到',
      }
    case 'compute':
      return {
        icon: <Icon name="calc" />,
        title: '计算',
        body: data.result_exact && (
          <div className="formula">
            {args.expression} = <b>{data.result_exact}</b>
          </div>
        ),
        detail: data.result && `保留 ${data.precision} 位：${data.result}`,
      }
    case 'date_calc':
      return {
        icon: <Icon name="calc" />,
        title: '日期计算',
        detail: data.date && `${args.base_date} 起 ${args.duration} ${args.unit} → ${data.date}`,
      }
    case 'write_note':
      return {
        icon: <Icon name="note" />,
        title: '整理笔记',
        body: data.note && (
          <>
            <div className="compact-rule">上下文压缩为这份笔记</div>
            <div className="note">
              <div className="note-head">工作笔记 revision={data.revision}</div>
              {data.note}
            </div>
          </>
        ),
      }
    case 'finalize':
      return {
        icon: CHECK,
        title: (
          <>
            提交答案 <span className="mono">{(data.submitted ?? args.answers ?? []).join('、')}</span>
          </>
        ),
        detail: step.result.ok ? '格式校验通过' : undefined,
      }
    case 'escalate':
      return {
        icon: <Icon name="alert" />,
        title: '放弃作答',
        detail: `${args.reason_code}：${args.detail}`,
      }
    default:
      return { icon: <Icon name="note" />, title: step.tool_name }
  }
}

export function ThoughtItem({ text, current = false }: { text: string; current?: boolean }) {
  return (
    <li className={current ? 'step is-current' : 'step'}>
      <div className="reveal-inner">
        <div className="step-inner">
          <span className="step-node">
            <i className="thought-dot" />
          </span>
          <p className="thought-text">{text}</p>
        </div>
      </div>
    </li>
  )
}

export function ToolItem({ step, current }: { step: StepEvent; current: boolean }) {
  const card = describe(step)
  const failed = step.result.ok === false
  const isCheck = step.tool_name === 'finalize' && !failed
  return (
    <li className={current ? 'step is-current' : 'step'} data-step={step.step_no}>
      <div className="reveal-inner">
        <div className="step-inner">
          <span className={isCheck ? 'step-node is-check' : 'step-node'}>{card.icon}</span>
          <div className="step-row">
            <span className="step-title">{card.title}</span>
          </div>
          {card.body}
          {failed ? (
            <div className="step-detail is-error">
              {String(step.result.error)}：{String(step.result.hint ?? step.result.detail ?? '')}
            </div>
          ) : (
            card.detail && <div className="step-detail">{card.detail}</div>
          )}
        </div>
      </div>
    </li>
  )
}
