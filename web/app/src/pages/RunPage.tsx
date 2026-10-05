/*
 * 一道题的页面：/runs/:id。先用 GET /runs/{id} 取题目本身（问题、模型、提交时间）。
 * run 不存在（或不是自己的）时后端返回 404，这里显示“找不到”页面。
 */

import { useEffect, useState } from 'react'
import { ApiError, getRun, type RunDetail } from '../api'
import { Composer } from '../components/Composer'
import { NotFound } from './NotFound'

type Loaded = { run: RunDetail } | { error: ApiError }

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

export function RunPage({ runId, onCreated }: { runId: number; onCreated: (runId: number) => void }) {
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

  const { run } = loaded
  return (
    <main className="main">
      <header className="main-head">
        <span className="main-title">{run.question}</span>
        <span className="main-meta">{run.model}</span>
      </header>
      <div className="thread">
        <div className="ask">
          <span className="ask-meta">提问 · {formatTime(run.created_at)}</span>
          <h1 className="bubble">{run.question}</h1>
        </div>
      </div>
      <Composer onCreated={onCreated} />
    </main>
  )
}
