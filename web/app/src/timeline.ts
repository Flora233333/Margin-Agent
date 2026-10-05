/*
 * 时间线：把 SSE 收到的事件，一条条合并成页面要显示的状态。纯函数，不碰浏览器，单元测试直接测（timeline.test.ts）。
 *
 * 两层事件（PLAN §5.4，后端见 live.py）：
 *   持久事件  attempt_queued / attempt_started / step / attempt_finished / attempt_failed，
 *             带递增的 seq，断线重连、刷新页面时服务端从断点补发；
 *   实时片段  delta：模型逐字输出的思考，不带 seq、不补发，只为“看着它在想”。
 *
 * 三条规则：
 *   1. 持久事件按 seq 去重：seq 不大于已收到的最后一个，直接忽略（重连时服务端可能补发重叠的部分）。
 *   2. 实时片段只认当前执行：片段带 (attempt_id, epoch)，必须和最近一次 attempt_started 的一致，
 *      否则是旧执行（重新生成之前的、或被接管的）还在路上的片段，丢掉。
 *   3. 某一轮的 step 事件到了，这一轮的实时片段整体换成 step 里的完整思考；之后再到的同一轮片段丢掉。
 */

export type AttemptStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface StepEvent {
  attempt_id: number
  step_no: number
  tool_name: string
  arguments: string
  result: Record<string, unknown>
  reasoning: string | null
}

export interface Delta {
  attempt_id: number
  epoch: number
  turn: number
  kind: 'reasoning' | 'content'
  text: string
}

export type StreamEvent =
  | { type: 'attempt_queued'; seq: number; data: { attempt_id: number; attempt_no: number; trigger: string } }
  | { type: 'attempt_started'; seq: number; data: { attempt_id: number; epoch: number } }
  | { type: 'step'; seq: number; data: StepEvent }
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

function updateAttempt(timeline: Timeline, id: number, change: (a: Attempt) => Attempt): Attempt[] {
  return timeline.attempts.map((a) => (a.id === id ? change(a) : a))
}

function applyDelta(timeline: Timeline, delta: Delta): Timeline {
  const { current } = timeline
  if (current?.attemptId !== delta.attempt_id || current.epoch !== delta.epoch) {
    return timeline // 规则 2：不是当前执行的片段
  }
  const attempts = updateAttempt(timeline, delta.attempt_id, (a) => {
    const lastStep = a.steps.at(-1)
    if (a.status !== 'running' || (lastStep && delta.turn <= lastStep.step_no)) {
      return a // 规则 3：这一轮已经有完整的 step 了，迟到的片段不再显示
    }
    // 新的一轮开始：上一轮的片段丢弃（正常情况下它已被 step 替换）
    const live = a.live?.turn === delta.turn ? a.live : { turn: delta.turn, reasoning: '', content: '' }
    return { ...a, live: { ...live, [delta.kind]: live[delta.kind] + delta.text } }
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
        final: null, violation: null, error: null,
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
    case 'step': {
      const step = event.data
      return {
        ...next,
        attempts: updateAttempt(timeline, step.attempt_id, (a) => ({
          ...a,
          steps: [...a.steps, step],
          live: a.live && a.live.turn > step.step_no ? a.live : null,
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

/** 最近一次执行已经结束（服务端会随即关闭 SSE），不需要再重连 */
export function isSettled(timeline: Timeline): boolean {
  const latest = timeline.attempts.at(-1)
  return latest !== undefined && (latest.status === 'completed' || latest.status === 'failed')
}
