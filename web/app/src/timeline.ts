/*
 * 时间线：把 SSE 收到的事件，一条条合并成页面要显示的状态。纯函数，不碰浏览器，单元测试直接测（timeline.test.ts）。
 *
 * 两层事件（PLAN §5.4，后端见 live.py）：
 *   持久事件  attempt_queued / attempt_started / run_understood / step / answer_written /
 *             attempt_finished / attempt_failed，带递增的 seq，断线重连、刷新页面时服务端从断点补发；
 *   实时片段  delta：模型逐字输出的思考和回答正文，不带 seq、不补发，只为“看着它在想、在写”。
 *
 * 三条规则：
 *   1. 持久事件按 seq 去重：seq 不大于已收到的最后一个，直接忽略（重连时服务端可能补发重叠的部分）。
 *   2. 实时片段只认当前执行：片段带 (attempt_id, epoch)，必须和最近一次 attempt_started 的一致，
 *      否则是旧执行（重新生成之前的、或被接管的）还在路上的片段，丢掉。
 *   3. 某一轮的 step 事件到了，这一轮的实时片段整体换成 step 里的完整思考；之后再到的同一轮片段丢掉。
 *      回答正文同理：answer_written 到了，逐字拼起来的正文换成校验后的（后端可能删掉了不存在的 [n]）。
 *
 * 交卷那一步（成功的 finalize / escalate）到了就记下 final，不等 attempt_finished：
 * 之后撰写回答还要十几秒，页面在这期间就要按“结论 → 正文”的顺序显示（Answer.tsx）。
 * 两处的内容相同（后端 loop.py：trace.final = {name, ...这一步的结果}）。
 */

export type AttemptStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface StepEvent {
  attempt_id: number
  step_no: number
  tool_name: string
  arguments: string
  result: Record<string, unknown>
  reasoning: string | null
  citation_no: number | null // 通过校验的引用的编号，由后端编（src/margin/citations.py）；其他步骤为 null
}

export interface Delta {
  attempt_id: number
  epoch: number
  turn: number
  kind: 'reasoning' | 'content' | 'answer' // answer：Harness 结束后撰写的回答正文（src/margin/compose.py）
  text: string
}

/** 理解题目的结果（src/margin/understand.py） */
export interface Understood {
  title: string
  label: string | null
  answer_format: string
}

/** 撰写的回答，后端校验过：text 里的 [n] 都有对应的来源；失败时只有 error，页面退回短答案 + 依据 */
export interface Written {
  text?: string
  citations?: number[]
  unverified?: string[] // 在问题、原文、计算、提交的答案里都找不到的数字
  error?: string
}

export type StreamEvent =
  | { type: 'attempt_queued'; seq: number; data: { attempt_id: number; attempt_no: number; trigger: string } }
  | { type: 'attempt_started'; seq: number; data: { attempt_id: number; epoch: number } }
  | { type: 'run_understood'; seq: number; data: Understood }
  | { type: 'step'; seq: number; data: StepEvent }
  | { type: 'answer_written'; seq: number; data: { attempt_id: number } & Written }
  | {
      type: 'attempt_finished'
      seq: number
      data: { attempt_id: number; final: Record<string, unknown> | null; violation: string | null }
    }
  | { type: 'attempt_failed'; seq: number; data: { attempt_id: number; error: string } }
  | { type: 'delta'; data: Delta }

/** 正在输出、还没有对应 step 事件的那一轮 */
export interface LiveTurn {
  turn: number
  reasoning: string
  content: string
}

export interface Attempt {
  id: number
  no: number
  trigger: string
  status: AttemptStatus
  steps: StepEvent[]
  live: LiveTurn | null
  understood: Understood | null // 这次执行里理解了题目（只有第一次执行有；重新生成沿用标题）
  answer: string // 正在逐字输出的回答正文；answer_written 到了清空
  written: Written | null
  final: Record<string, unknown> | null
  violation: string | null
  error: string | null
}

export interface Timeline {
  lastSeq: number // 已收到的最后一个持久事件的 seq，重连时从它之后补发
  attempts: Attempt[]
  current: { attemptId: number; epoch: number } | null // 最近一次 attempt_started
}

export const EMPTY_TIMELINE: Timeline = { lastSeq: 0, attempts: [], current: null }

/** 交卷的那一步：成功的 finalize（提交答案）或 escalate（放弃作答） */
function isHandIn(step: StepEvent): boolean {
  return (step.tool_name === 'finalize' || step.tool_name === 'escalate') && step.result.ok === true
}

/** Harness 已经交了答案或放弃作答：之后就是撰写回答 */
export function ended(attempt: Attempt): boolean {
  const last = attempt.steps.at(-1)
  return last !== undefined && isHandIn(last)
}

function updateAttempt(timeline: Timeline, id: number, change: (a: Attempt) => Attempt): Attempt[] {
  return timeline.attempts.map((a) => (a.id === id ? change(a) : a))
}

function applyDelta(timeline: Timeline, delta: Delta): Timeline {
  const { current } = timeline
  if (current?.attemptId !== delta.attempt_id || current.epoch !== delta.epoch) {
    return timeline // 规则 2：不是当前执行的片段
  }
  const attempts = updateAttempt(timeline, delta.attempt_id, (a) => {
    if (delta.kind === 'answer') {
      return a.status === 'running' && !a.written ? { ...a, answer: a.answer + delta.text } : a
    }
    const lastStep = a.steps.at(-1)
    if (a.status !== 'running' || (lastStep && delta.turn <= lastStep.step_no)) {
      return a // 规则 3：这一轮已经有完整的 step 了，迟到的片段不再显示
    }
    // 新的一轮开始：上一轮的片段丢弃（正常情况下它已被 step 替换）
    const live = a.live?.turn === delta.turn ? a.live : { turn: delta.turn, reasoning: '', content: '' }
    const kind = delta.kind as 'reasoning' | 'content'
    return { ...a, live: { ...live, [kind]: live[kind] + delta.text } }
  })
  return { ...timeline, attempts }
}

export function applyEvent(timeline: Timeline, event: StreamEvent): Timeline {
  if (event.type === 'delta') {
    return applyDelta(timeline, event.data)
  }
  if (event.seq <= timeline.lastSeq) {
    return timeline // 规则 1：重复的持久事件
  }
  const next = { ...timeline, lastSeq: event.seq }
  switch (event.type) {
    case 'attempt_queued': {
      const { attempt_id, attempt_no, trigger } = event.data
      const attempt: Attempt = {
        id: attempt_id, no: attempt_no, trigger, status: 'queued', steps: [], live: null,
        understood: null, answer: '', written: null, final: null, violation: null, error: null,
      }
      return { ...next, attempts: [...timeline.attempts, attempt] }
    }
    case 'attempt_started': {
      const { attempt_id, epoch } = event.data
      return {
        ...next,
        current: { attemptId: attempt_id, epoch },
        attempts: updateAttempt(timeline, attempt_id, (a) => ({ ...a, status: 'running', live: null })),
      }
    }
    case 'run_understood': {
      // 持久事件里没有 attempt_id：理解题目发生在当时正在执行的那一次
      const attemptId = timeline.current?.attemptId ?? -1
      return { ...next, attempts: updateAttempt(timeline, attemptId, (a) => ({ ...a, understood: event.data })) }
    }
    case 'answer_written': {
      const { attempt_id, ...written } = event.data
      return { ...next, attempts: updateAttempt(timeline, attempt_id, (a) => ({ ...a, written, answer: '' })) }
    }
    case 'step': {
      const step = event.data
      return {
        ...next,
        attempts: updateAttempt(timeline, step.attempt_id, (a) => ({
          ...a,
          steps: [...a.steps, step],
          live: a.live && a.live.turn > step.step_no ? a.live : null,
          final: isHandIn(step) ? { name: step.tool_name, ...(step.result.data as Record<string, unknown>) } : a.final,
        })),
      }
    }
    case 'attempt_finished': {
      const { attempt_id, final, violation } = event.data
      return {
        ...next,
        attempts: updateAttempt(timeline, attempt_id, (a) => ({
          ...a, status: 'completed', final, violation, live: null,
        })),
      }
    }
    case 'attempt_failed': {
      const { attempt_id, error } = event.data
      return {
        ...next,
        attempts: updateAttempt(timeline, attempt_id, (a) => ({ ...a, status: 'failed', error, live: null })),
      }
    }
  }
}

/** 题目的理解结果：在哪次执行里得到的都一样（只有一次） */
export function understoodOf(timeline: Timeline): Understood | null {
  return timeline.attempts.find((a) => a.understood)?.understood ?? null
}

/** 最近一次执行已经结束（服务端会随即关闭 SSE），不需要再重连 */
export function isSettled(timeline: Timeline): boolean {
  const latest = timeline.attempts.at(-1)
  return latest !== undefined && (latest.status === 'completed' || latest.status === 'failed')
}
