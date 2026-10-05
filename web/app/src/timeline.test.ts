import { describe, expect, test } from 'vitest'
import { applyEvent, EMPTY_TIMELINE, isSettled, type StreamEvent, type Timeline } from './timeline'

function play(events: StreamEvent[], from: Timeline = EMPTY_TIMELINE): Timeline {
  return events.reduce(applyEvent, from)
}

const queued = (seq: number, attempt_id: number, attempt_no = 1): StreamEvent => ({
  type: 'attempt_queued', seq, data: { attempt_id, attempt_no, trigger: attempt_no === 1 ? 'submit' : 'regenerate' },
})
const started = (seq: number, attempt_id: number, epoch = 1): StreamEvent => ({
  type: 'attempt_started', seq, data: { attempt_id, epoch },
})
const step = (seq: number, attempt_id: number, step_no: number, reasoning = `完整思考 ${step_no}`): StreamEvent => ({
  type: 'step', seq,
  data: { attempt_id, step_no, tool_name: 'search_docs', arguments: '{}', result: { ok: true }, reasoning },
})
const delta = (attempt_id: number, epoch: number, turn: number, text: string): StreamEvent => ({
  type: 'delta', data: { attempt_id, epoch, turn, kind: 'reasoning', text },
})
const finished = (seq: number, attempt_id: number): StreamEvent => ({
  type: 'attempt_finished', seq, data: { attempt_id, final: { submitted: ['1'] }, violation: null },
})

describe('持久事件', () => {
  test('断线重连后服务端补发了重叠的事件，步骤不会重复显示', () => {
    const before = play([queued(1, 7), started(2, 7), step(3, 7, 0)])
    // 重连时起点算错或网络重放，3 又来了一次
    const after = play([step(3, 7, 0), step(4, 7, 1)], before)
    expect(after.attempts[0].steps.map((s) => s.step_no)).toEqual([0, 1])
    expect(after.lastSeq).toBe(4)
  })

  test('回放一道重新生成过的题：第一次执行结束后还有第二次，不能当成已结束而断开推流', () => {
    const replay = play([queued(1, 7), started(2, 7), finished(3, 7), queued(4, 8, 2)])
    expect(isSettled(replay)).toBe(false)
    expect(isSettled(play([started(5, 8), finished(6, 8)], replay))).toBe(true)
  })
})

describe('实时片段', () => {
  test('同一轮的片段拼接起来，逐字显示', () => {
    const t = play([queued(1, 7), started(2, 7), delta(7, 1, 0, '先找'), delta(7, 1, 0, '年报')])
    expect(t.attempts[0].live).toEqual({ turn: 0, reasoning: '先找年报', content: '' })
  })

  test('这一轮的 step 到了，片段换成完整思考；之后迟到的同一轮片段丢掉', () => {
    const t = play([
      queued(1, 7), started(2, 7), delta(7, 1, 0, '先找'),
      step(3, 7, 0, '先找年报。'), delta(7, 1, 0, '年报'),
    ])
    expect(t.attempts[0].live).toBeNull()
    expect(t.attempts[0].steps[0].reasoning).toBe('先找年报。')
  })

  test('重新生成后，旧执行还在路上的片段不显示', () => {
    const t = play([
      queued(1, 7), started(2, 7), finished(3, 7), queued(4, 8, 2), started(5, 8),
      delta(7, 1, 3, '旧执行的片段'),
    ])
    expect(t.attempts.every((a) => a.live === null)).toBe(true)
  })

  test('同一次执行被接管（epoch 变了），旧 worker 的片段不显示', () => {
    // M4：租约过期后别的 worker 接管同一个 attempt，epoch 从 1 变成 2
    const t = play([queued(1, 7), started(2, 7, 1), started(3, 7, 2), delta(7, 1, 0, '旧 worker')])
    expect(t.attempts[0].live).toBeNull()
    expect(play([delta(7, 2, 0, '新 worker')], t).attempts[0].live?.reasoning).toBe('新 worker')
  })
})
