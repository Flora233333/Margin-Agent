/*
 * 过程区：一次执行（attempt）的思考与工具调用，可折叠。执行中默认展开、结束后自动收起；
 * 用户手动点过之后就按用户的来。
 *
 * 每一步先显示思考（step.reasoning），再显示工具调用；最后一轮还没有 step 事件时，
 * 显示实时片段拼起来的思考（live），step 一到就被完整思考替换（规则见 timeline.ts）。
 */

import { useState } from 'react'
import type { Attempt } from '../timeline'
import { Icon } from './Icons'
import { ThoughtItem, ToolItem } from './StepItem'

function label(attempt: Attempt): string {
  const n = attempt.steps.length
  switch (attempt.status) {
    case 'queued':
      return '排队中，等待 worker 领取…'
    case 'running':
      return attempt.live ? `正在思考 · 已完成 ${n} 步` : `正在执行 · 已完成 ${n} 步`
    case 'completed':
      return `思考与检索 · ${n} 步`
    case 'failed':
      return `思考与检索 · ${n} 步 · 未完成`
  }
}

export function Work({ attempt, citeNos }: { attempt: Attempt; citeNos: Map<number, number> }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  const active = attempt.status === 'queued' || attempt.status === 'running'
  const open = userOpen ?? active
  const running = attempt.status === 'running'
  // 实时片段里思考（reasoning）和正文（content，模型在调用工具前说的话）都显示
  const live = attempt.live ? [attempt.live.reasoning, attempt.live.content].filter(Boolean).join('\n\n') : ''

  const classes = ['work', open && 'is-open', active && 'is-running'].filter(Boolean).join(' ')
  return (
    <section className={classes}>
      <button className="work-head" type="button" aria-expanded={open} onClick={() => setUserOpen(!open)}>
        <Icon name="chev" className="icon chev" />
        <span className="work-label">
          <span>{label(attempt)}</span>
        </span>
        <span className="pulse" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
      </button>
      <div className="work-body-wrap reveal">
        <div className="reveal-inner">
          <div className="work-body">
            <ol className="steps">
              {attempt.steps.map((step, i) => {
                const last = running && !live && i === attempt.steps.length - 1
                return [
                  step.reasoning && <ThoughtItem key={`t${step.step_no}`} text={step.reasoning} />,
                  <ToolItem key={`s${step.step_no}`} step={step} current={last} citeNo={citeNos.get(step.step_no)} />,
                ]
              })}
              {/* key 和 step 到达后的完整思考相同：React 原地换掉文字，不会删掉重建、重播出现动画 */}
              {live && <ThoughtItem key={`t${attempt.live!.turn}`} text={live} current />}
            </ol>
          </div>
        </div>
      </div>
    </section>
  )
}
