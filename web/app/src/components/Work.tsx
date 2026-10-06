/*
 * 过程区：一次执行（attempt）的思考与工具调用，可折叠。执行中默认展开、结束后自动收起；
 * 用户手动点过之后就按用户的来。
 *
 * 每一步先显示思考（step.reasoning），再显示工具调用；最后一轮还没有 step 事件时，
 * 显示实时片段拼起来的思考（live），step 一到就被完整思考替换（规则见 timeline.ts）。
 * M2.5 起前后各多一项：第一项“理解题目”（标题、判断的答案格式），最后一项“撰写回答”。
 */

import { type ReactNode, useState } from 'react'
import { type AnswerFormat, FORMAT_LABEL } from '../api'
import type { Attempt } from '../timeline'
import { Icon } from './Icons'
import { StageItem, ThoughtItem, ToolItem, UnderstandItem } from './StepItem'

/** Harness 已经交了答案或放弃作答：之后就是撰写回答 */
function ended(attempt: Attempt): boolean {
  const last = attempt.steps.at(-1)
  return !!last && (last.tool_name === 'finalize' || last.tool_name === 'escalate') && last.result.ok === true
}

function label(attempt: Attempt): string {
  const n = attempt.steps.length
  switch (attempt.status) {
    case 'queued':
      return '排队中，等待 worker 领取…'
    case 'running':
      if (ended(attempt)) {
        return `正在撰写回答 · 已完成 ${n} 步`
      }
      return attempt.live ? `正在思考 · 已完成 ${n} 步` : `正在执行 · 已完成 ${n} 步`
    case 'completed':
      return `思考与检索 · ${n} 步`
    case 'failed':
      return `思考与检索 · ${n} 步 · 未完成`
  }
}

/**
 * 过程区标题：文字变了时，旧文字上移淡出、新文字从下方淡入（base.css 的 .is-leaving / .is-entering，两段叠在同一格里）。
 * 淡入播完要去掉 is-entering：执行中标题的“扫光”动画只加在没有这两个类的文字上。
 */
function SwapLabel({ text }: { text: string }) {
  const [label, setLabel] = useState({ text, leaving: '', n: 0, entering: false })
  if (text !== label.text) {
    setLabel({ text, leaving: label.text, n: label.n + 1, entering: true })
  }
  return (
    <span className="work-label">
      {label.leaving && (
        <span key={`out${label.n}`} className="is-leaving" onAnimationEnd={() => setLabel((l) => ({ ...l, leaving: '' }))}>
          {label.leaving}
        </span>
      )}
      <span
        key={`in${label.n}`}
        className={label.entering ? 'is-entering' : undefined}
        onAnimationEnd={(e) => e.animationName === 'label-in' && setLabel((l) => ({ ...l, entering: false }))}
      >
        {label.text}
      </span>
    </span>
  )
}

interface Props {
  attempt: Attempt
  citeNos: Map<number, number>
  // 这次执行正在理解题目（第一次执行、标题还没有）：第一项先占位转圈，结果到了原地换成标题
  understanding: boolean
}

export function Work({ attempt, citeNos, understanding }: Props) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const active = attempt.status === 'queued' || attempt.status === 'running'
  const open = userOpen ?? active
  const running = attempt.status === 'running'
  // 实时片段里思考（reasoning）和正文（content，模型在调用工具前说的话）都显示
  const live = attempt.live ? [attempt.live.reasoning, attempt.live.content].filter(Boolean).join('\n\n') : ''
  const understood = attempt.understood
  // 旧题（M2.5 之前）结束时没有撰写的回答，不显示这一项
  const composing = attempt.answer !== '' || attempt.written !== null || (running && ended(attempt))
  const written = attempt.written

  const items: ReactNode[] = []
  // 理解题目和 Harness 并行：执行一开始就占住第一项（转圈），下面的思考照常出现；
  // 结果到了同一个条目（key 不变）原地换成标题，不会在顶上突然插进一条、把下面整体往下推
  if (understood) {
    const detail = [`答案格式：${FORMAT_LABEL[understood.answer_format as AnswerFormat]}`, understood.label]
    items.push(
      <UnderstandItem key="understood" title={<>理解题目 <q>{understood.title}</q></>}
        detail={detail.filter(Boolean).join(' · ')} done />,
    )
  } else if (understanding) {
    items.push(<UnderstandItem key="understood" title="正在理解题目…" done={false} />)
  }
  attempt.steps.forEach((step, i) => {
    const last = running && !live && !composing && i === attempt.steps.length - 1
    if (step.reasoning) {
      items.push(<ThoughtItem key={`t${step.step_no}`} text={step.reasoning} />)
    }
    items.push(<ToolItem key={`s${step.step_no}`} step={step} current={last} citeNo={citeNos.get(step.step_no)} />)
  })
  if (live) {
    // key 和这一轮 step 到达后的完整思考相同（t + 轮次）：到达时原地换成完整思考
    items.push(<ThoughtItem key={`t${attempt.live!.turn}`} text={live} current />)
  }
  if (composing) {
    const detail = written && (written.error ? '没有写成，下面显示提交的答案和依据' : `引用 ${written.citations?.length ?? 0} 处`)
    items.push(
      <StageItem key="compose" icon="note" title={written ? '撰写回答' : '正在撰写回答…'}
        detail={detail || undefined} current={!written} />,
    )
  }

  const classes = ['work', open && 'is-open', active && 'is-running'].filter(Boolean).join(' ')
  return (
    <section className={classes}>
      <button className="work-head" type="button" aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <Icon name="chev" className="icon chev" />
        <SwapLabel text={label(attempt)} />
        <span className="pulse" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </button>
      <div className="work-body-wrap reveal">
        <div className="reveal-inner">
          <div className="work-body">
            {/*
              所有条目放在同一个数组里渲染（items），不要拆成“步骤数组 + 单独的实时思考”几块：
              React 只在同一个数组的兄弟之间按 key 认出“同一个元素”。实时思考（key t3）原来单独占一个位置，
              step 3 到达后完整思考（也是 t3）出现在步骤数组里，位置不同，React 就删掉旧的、新建一个，
              新建的条目重播 0.5 秒的出现动画——长长一段思考从上往下重新展开一遍，整个过程区跳一下。
              放进同一个数组后，t3 原地保留，只换文字。
            */}
            <ol className="steps">{items}</ol>
          </div>
        </div>
      </div>
    </section>
  )
}
