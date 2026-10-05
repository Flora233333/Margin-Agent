/*
 * 订阅一道题的事件流（SSE），返回合并好的时间线。用法：const { timeline, connection } = useRunStream(runId, ...)
 * 一个组件实例只对应一个 runId（RunPage 用 key={runId} 保证换题时整个重建）。
 *
 * EventSource 是浏览器自带的 SSE 客户端，它自己会断线重连，并在请求头 Last-Event-ID 里带上最后收到的 id。
 * 这里还要补四件它不管的事：
 *   1. 正常结束：服务端推完结束事件就关闭连接，EventSource 会把这当成“断线”，过几秒又连上来，
 *      然后服务端又立刻关闭……所以一旦最近一次执行已经结束（isSettled），就主动 close()。
 *   2. 放弃重连：重连时如果收到的不是 200 的事件流（例如 API 重启期间，Vite 代理返回 502），
 *      EventSource 会永久放弃（readyState 变成 CLOSED）。这时自己隔 2 秒新建一个，用 ?after=lastSeq 从断点接上。
 *   3. 半死的连接：API 进程被杀时，中间的代理不一定把浏览器这一侧的连接关掉（M2 联调时 Vite 代理就是这样），
 *      浏览器以为连接还在，永远等下去，也不会触发重连。服务端空闲时每 10 秒发一个心跳（event: ping），
 *      所以超过 25 秒什么都没收到，就认定连接已断，关掉重建。
 *   4. 重新打开：用户点“重新生成”时流已经关了，reopen() 新建一个，同样从 lastSeq 之后接收。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { eventsUrl } from './api'
import { applyEvent, EMPTY_TIMELINE, isSettled, type StreamEvent, type Timeline } from './timeline'

const PERSISTENT = ['attempt_queued', 'attempt_started', 'step', 'attempt_finished', 'attempt_failed'] as const
const RETRY_MS = 2000
const SILENCE_MS = 25_000 // 服务端心跳间隔 10 秒（api.py 的 SSE_FALLBACK_SECONDS），留出两次多一点的余量

// connecting：正在建立；open：已连上；retrying：断了，正在重连；closed：执行结束，已主动关闭
export type Connection = 'connecting' | 'open' | 'retrying' | 'closed'

export function useRunStream(runId: number, onSettled: () => void) {
  const [timeline, setTimeline] = useState<Timeline>(EMPTY_TIMELINE)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [generation, setGeneration] = useState(0) // 加 1 就重新打开一个 EventSource
  // 事件处理函数里要读“最新的”时间线（判断是否已结束、从哪个 seq 接上），state 在下次渲染前读不到新值，所以另存一份在 ref 里
  const timelineRef = useRef(EMPTY_TIMELINE)
  // 回调也存进 ref：每次渲染传进来的是新函数，但不应该因此关掉重开 EventSource
  const onSettledRef = useRef(onSettled)
  useEffect(() => {
    onSettledRef.current = onSettled
  })

  useEffect(() => {
    let source: EventSource
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let silenceTimer: ReturnType<typeof setTimeout> | undefined

    /** 收到任何东西（持久事件、片段、心跳）都说明连接活着：重新开始计时（第 3 件事） */
    function alive() {
      clearTimeout(silenceTimer)
      silenceTimer = setTimeout(() => {
        source.close()
        setConnection('retrying')
        open()
      }, SILENCE_MS)
    }

    function apply(event: StreamEvent) {
      alive()
      timelineRef.current = applyEvent(timelineRef.current, event)
      setTimeline(timelineRef.current)
    }

    function open() {
      setConnection('connecting')
      source = new EventSource(eventsUrl(runId, timelineRef.current.lastSeq))
      alive() // 连接一直建立不起来（请求挂住）也算断了
      source.onopen = () => {
        alive()
        setConnection('open')
      }
      for (const type of PERSISTENT) {
        source.addEventListener(type, (e) => {
          // 持久事件的 SSE id 就是 seq
          apply({ type, seq: Number(e.lastEventId), data: JSON.parse(e.data) } as StreamEvent)
        })
      }
      source.addEventListener('delta', (e) => apply({ type: 'delta', data: JSON.parse(e.data) }))
      source.addEventListener('ping', alive)
      source.onerror = () => {
        if (isSettled(timelineRef.current)) {
          clearTimeout(silenceTimer)
          source.close() // 第 1 件事：正常结束
          setConnection('closed')
          onSettledRef.current()
        } else if (source.readyState === EventSource.CLOSED) {
          clearTimeout(silenceTimer)
          setConnection('retrying') // 第 2 件事：浏览器放弃了，自己重建
          retryTimer = setTimeout(open, RETRY_MS)
        } else {
          setConnection('retrying') // 浏览器正在自动重连（带 Last-Event-ID）；挂住的话由心跳计时兜底
        }
      }
    }

    open()
    return () => {
      source.close()
      clearTimeout(retryTimer)
      clearTimeout(silenceTimer)
    }
  }, [runId, generation])

  const reopen = useCallback(() => setGeneration((g) => g + 1), [])
  return { timeline, connection, reopen }
}
