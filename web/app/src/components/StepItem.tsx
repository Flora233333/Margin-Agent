/*
 * 时间线里的一条：思考（灰色小点 + 一段文字）或一次工具调用（图标 + 标题 + 一行结果）。
 * 每种工具显示什么，按 harness 的工具结果字段来（src/margin/harness/tools/）：
 * 结果统一是 {ok: true, data: {...}} 或 {ok: false, error: 错误码, hint: 怎么改}。
 */

import { type ReactNode, useState } from 'react'
import { type AnswerFormat, FORMAT_LABEL } from '../api'
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

function describe(step: StepEvent, citeNo?: number): Card {
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
        title: <>引用 {citeNo && `[${citeNo}] `}{q(args.quote)}</>,
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
        detail: !step.result.ok
          ? undefined
          : data.answer_format_fallback
            ? `按文本收下（自动判断的格式是“${FORMAT_LABEL[data.answer_format_fallback as AnswerFormat]}”，答案不是这种形式）`
            : '格式校验通过',
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

const FADING = 40 // 最多保留最近多少段单独淡入；更早的已经播完动画，合并成普通文字，页面里不会堆上千个 <span>

/**
 * 正在输出的思考：每次新到的一段文字包在 <span class="tok"> 里，插入时播放 0.4 秒淡入（base.css 的 .tok）。
 * cuts 记下每一段的起点。“记住上一次渲染的值、渲染时比较”是 React 文档推荐的写法（不需要 effect）：
 * 文字变了就在渲染过程中更新 state，React 会立刻用新 state 重新渲染这个组件。
 */
function StreamingText({ text }: { text: string }) {
  const [seen, setSeen] = useState({ text, cuts: [] as number[] })
  if (text !== seen.text) {
    // 新文字是旧文字的延续：在旧文字的结尾切一刀；不是延续（极少见，例如被完整思考替换）就从头算
    const cuts = text.startsWith(seen.text) ? [...seen.cuts, seen.text.length].slice(-FADING) : []
    setSeen({ text, cuts })
  }
  const cuts = seen.text === text ? seen.cuts : []
  const first = cuts[0] ?? text.length
  return (
    <p className="thought-text">
      {text.slice(0, first)}
      {cuts.map((start, i) => (
        <span className="tok" key={start}>
          {text.slice(start, cuts[i + 1] ?? text.length)}
        </span>
      ))}
    </p>
  )
}

export function ThoughtItem({ text, current = false }: { text: string; current?: boolean }) {
  return (
    <li className={current ? 'step is-current' : 'step'}>
      <div className="reveal-inner">
        <div className="step-inner">
          <span className="step-node">
            <i className="thought-dot" />
          </span>
          {/* 结束的思考也用 StreamingText：step 到达、current 变成 false 时 <p> 保持同一个元素，
              已经淡入完的文字原样留着；打开页面时一次拿到的完整思考没有“切口”，就是普通文字 */}
          <StreamingText text={text} />
        </div>
      </div>
    </li>
  )
}

interface ToolProps {
  step: StepEvent
  current: boolean
  citeNo?: number // 通过校验的引用的编号；右侧来源旁注在回答出来之前对齐到这一步（data-cite）
}

// 工具错误的提示原文是写给模型的英文（harness 的 hint）；产品规则加的那一条给用户看中文
const ERROR_TEXT: Record<string, string> = {
  citation_required: '还没有引用原文就提交了：提醒模型先用 cite 引用支持答案的原文（只提醒一次，再交就照收）',
}

export function ToolItem({ step, current, citeNo }: ToolProps) {
  const card = describe(step, citeNo)
  const failed = step.result.ok === false
  const isCheck = step.tool_name === 'finalize' && !failed
  return (
    <li className={current ? 'step is-current' : 'step'} data-cite={citeNo}>
      <div className="reveal-inner">
        <div className="step-inner">
          <span className={isCheck ? 'step-node is-check' : 'step-node'}>{card.icon}</span>
          <div className="step-row">
            <span className="step-title">{card.title}</span>
          </div>
          {card.body}
          {failed ? (
            <div className="step-detail is-error">
              {ERROR_TEXT[String(step.result.error)] ??
                `${String(step.result.error)}：${String(step.result.hint ?? step.result.detail ?? '')}`}
            </div>
          ) : (
            card.detail && <div className="step-detail">{card.detail}</div>
          )}
        </div>
      </div>
    </li>
  )
}

interface StageProps {
  icon: string
  title: ReactNode
  detail?: string
  current: boolean
}

/** 不是工具调用的两个环节（M2.5）：第一项“理解题目”，最后一项“撰写回答”。样子和工具调用一样 */
export function StageItem({ icon, title, detail, current }: StageProps) {
  return (
    <li className={current ? 'step is-current' : 'step'}>
      <div className="reveal-inner">
        <div className="step-inner">
          <span className="step-node">
            <Icon name={icon} />
          </span>
          <div className="step-row">
            <span className="step-title">{title}</span>
          </div>
          {detail && <div className="step-detail">{detail}</div>}
        </div>
      </div>
    </li>
  )
}

interface UnderstandProps {
  title: ReactNode
  detail?: string
  done: boolean
}

/**
 * 第一项“理解题目”。图标是一个圆：理解中是一段旋转的弧；理解完弧闭合成整圆、圆心点出一个点（像对准了靶心）。
 * 不用勾：勾留给最后的“提交答案”。
 * 两种状态是同一个 SVG，只换类名，弧长（stroke-dasharray）用过渡从一段长到一整圈；
 * 旋转动画一直开着——整圆转起来看不出来，停掉的话弧会在闭合的那一刻跳回起始角度（app.css 的 .target-draw）。
 */
export function UnderstandItem({ title, detail, done }: UnderstandProps) {
  return (
    <li className="step">
      <div className="reveal-inner">
        <div className="step-inner">
          <span className={done ? 'step-node is-target is-done' : 'step-node is-target'}>
            <svg className="target-draw" viewBox="0 0 20 20" aria-hidden="true">
              <circle className="target-ring" cx="10" cy="10" r="7" pathLength={1} />
              <circle className="target-dot" cx="10" cy="10" r="2.2" />
            </svg>
          </span>
          <div className="step-row">
            <span className="step-title">{title}</span>
          </div>
          {detail && <div className="step-detail">{detail}</div>}
        </div>
      </div>
    </li>
  )
}
