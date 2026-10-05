/*
 * 一道题的页面：/runs/:id。
 *   1. 先用 GET /runs/{id} 取题目本身（问题、模型、提交时间）。run 不存在（或不是自己的）时后端返回 404，显示“找不到”。
 *   2. 再由 RunView 订阅 SSE（useRunStream），从 seq 0 开始收：已经发生过的事件服务端一次补发完，
 *      之后的边发生边推。所以刷新页面、从历史列表点进来、正在执行中，都是同一条代码路径。
 */

import { useEffect, useRef, useState } from 'react'
import { ApiError, getRun, type RunDetail } from '../api'
import { Composer } from '../components/Composer'
import { Work } from '../components/Work'
import { useRunStream } from '../useRunStream'
import { NotFound } from './NotFound'

type Loaded = { run: RunDetail } | { error: ApiError }

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

interface Props {
  runId: number
  onCreated: (runId: number) => void
  onRunsChanged: () => void
}

export function RunPage({ runId, onCreated, onRunsChanged }: Props) {
  // 记下“这份结果属于哪个 runId”：切换到另一道题时，旧结果不会在新请求返回之前被当成新题显示
  const [loaded, setLoaded] = useState<{ runId: number } & Loaded>()

  useEffect(() => {
    let current = true // 请求返回前用户已经切到别的题，就丢弃这次结果
    getRun(runId).then(
      (run) => current && setLoaded({ runId, run }),
      (error: ApiError) => current && setLoaded({ runId, error }),
    )
    return () => {
      current = false
    }
  }, [runId])

  if (loaded?.runId !== runId) {
    return <main className="main" aria-busy="true" />
  }
  if ('error' in loaded) {
    return loaded.error.status === 404 ? (
      <NotFound message="这道题不存在，或者不属于当前账号。" />
    ) : (
      <main className="main">
        <div className="thread">
          <p className="form-error" role="alert">{loaded.error.message}</p>
        </div>
      </main>
    )
  }
  // key：换一道题时 RunView 整个重建，useRunStream 的状态从空开始
  return <RunView key={runId} run={loaded.run} onCreated={onCreated} onRunsChanged={onRunsChanged} />
}

interface ViewProps {
  run: RunDetail
  onCreated: (runId: number) => void
  onRunsChanged: () => void
}

function RunView({ run, onCreated, onRunsChanged }: ViewProps) {
  // 打开页面时题目还在执行：等它结束要刷新左侧列表的状态点。打开时已结束的题不用（列表本来就是对的）
  const watching = useRef(run.status === 'queued' || run.status === 'running')
  const { timeline, connection } = useRunStream(run.id, () => {
    if (watching.current) {
      watching.current = false
      onRunsChanged()
    }
  })

  return (
    <main className="main">
      <header className="main-head">
        <span className="main-title">{run.question}</span>
        {connection === 'retrying' && <span className="main-meta is-warn">连接中断，正在重连…</span>}
        <span className="main-meta">{run.model}</span>
      </header>
      <div className="thread">
        <div className="ask">
          <span className="ask-meta">提问 · {formatTime(run.created_at)}</span>
          <h1 className="bubble">{run.question}</h1>
        </div>
        {timeline.attempts.map((attempt) => (
          <div className="attempt" key={attempt.id}>
            {timeline.attempts.length > 1 && (
              <div className="attempt-head">
                第 {attempt.no} 次执行{attempt.trigger === 'regenerate' && '（重新生成）'}
              </div>
            )}
            <Work attempt={attempt} />
          </div>
        ))}
      </div>
      <Composer onCreated={onCreated} />
    </main>
  )
}
