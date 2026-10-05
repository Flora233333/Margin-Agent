/*
 * 一道题的页面：/runs/:id。
 *   1. 先用 GET /runs/{id} 取题目本身（问题、模型、提交时间）。run 不存在（或不是自己的）时后端返回 404，显示“找不到”。
 *   2. 再由 RunView 订阅 SSE（useRunStream），从 seq 0 开始收：已经发生过的事件服务端一次补发完，
 *      之后的边发生边推。所以刷新页面、从历史列表点进来、正在执行中，都是同一条代码路径。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, getRun, regenerate, type RunDetail } from '../api'
import { citationsOf } from '../citations'
import { Answer } from '../components/Answer'
import { Composer } from '../components/Composer'
import { Failure } from '../components/Failure'
import { Icon } from '../components/Icons'
import { Sources } from '../components/Sources'
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
  const [actionError, setActionError] = useState('') // 重新生成失败（409 等）的提示
  // 打开页面时题目还在执行：等它结束要刷新左侧列表的状态点。打开时已结束的题不用（列表本来就是对的）
  const watching = useRef(run.status === 'queued' || run.status === 'running')
  const { timeline, connection, reopen } = useRunStream(run.id, () => {
    setActionError('') // 例如执行中点重新生成得到的 409 提示，执行结束后就过时了
    if (watching.current) {
      watching.current = false
      onRunsChanged()
    }
  })
  const thread = useRef<HTMLDivElement>(null)
  const [linked, setLinked] = useState<number | null>(null) // 鼠标停在哪个引用上：[n] 和旁注一起高亮
  const [flash, setFlash] = useState({ no: 0, count: 0 }) // 点了哪个 [n]：对应旁注展开并闪一下

  /**
   * 重新生成：后端新建一次执行（旧的执行和步骤都保留），前端重新打开事件流，从已收到的最后一个 seq 之后接着收。
   * 按钮一直可以点，执行中点了由后端返回 409“这道题还在执行中”：是否允许以后端为准，
   * 前端的状态可能是旧的（例如另一个标签页刚点过重新生成）。
   */
  async function onRegenerate() {
    setActionError('')
    try {
      await regenerate(run.id)
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e))
      return
    }
    watching.current = true // 这次执行结束时要刷新左侧列表
    onRunsChanged() // 列表里这道题变回“执行中”
    reopen()
  }

  // 来源只显示最近一次执行的引用；更早的执行只保留过程和结论，不再标引用编号
  const latest = timeline.attempts.at(-1)
  const citations = useMemo(() => citationsOf(latest?.steps ?? []), [latest?.steps])
  const citeNos = useMemo(() => new Map(citations.map((c) => [c.stepNo, c.no])), [citations])
  useFollowBottom(latest?.status === 'running', timeline)

  return (
    <>
      <main className="main">
        <header className="main-head">
          <span className="main-title">{run.question}</span>
          {connection === 'retrying' && <span className="main-meta is-warn">连接中断，正在重连…</span>}
          <span className="main-meta">{run.model}</span>
          <button className="icon-btn" type="button" onClick={() => void onRegenerate()}>
            <Icon name="redo" />
            重新生成
          </button>
        </header>
        <div className="thread" ref={thread}>
          <div className="ask">
            <span className="ask-meta">提问 · {formatTime(run.created_at)}</span>
            <h1 className="bubble">{run.question}</h1>
          </div>
          {timeline.attempts.map((attempt) => {
            const isLatest = attempt === latest
            return (
              <div className="attempt" key={attempt.id}>
                {timeline.attempts.length > 1 && (
                  <div className="attempt-head">
                    第 {attempt.no} 次执行{attempt.trigger === 'regenerate' && '（重新生成）'}
                  </div>
                )}
                <Work attempt={attempt} citeNos={isLatest ? citeNos : NO_CITES} />
                {attempt.status === 'failed' && <Failure error={attempt.error} />}
                {attempt.status === 'completed' && (
                  <Answer
                    attempt={attempt}
                    format={run.answer_format}
                    options={run.options}
                    model={run.model}
                    citations={isLatest ? citations : null}
                    linked={linked}
                    onCiteHover={setLinked}
                    onCiteClick={(no) => setFlash((f) => ({ no, count: f.count + 1 }))}
                  />
                )}
              </div>
            )
          })}
          {actionError && (
            <p className="form-error" role="alert">
              <Icon name="alert" />
              {actionError}
            </p>
          )}
        </div>
        <Composer onCreated={onCreated} />
      </main>
      <Sources citations={citations} thread={thread} linked={linked} flash={flash} onHover={setLinked} />
    </>
  )
}

const NO_CITES = new Map<number, number>()

/** 当前负责滚动的元素：简洁风宽屏时是 .app，窄屏时 .app 不滚动，滚的是整个页面 */
function scroller(): Element {
  const app = document.querySelector('.app')!
  return getComputedStyle(app).overflowY === 'auto' ? app : document.scrollingElement!
}

/**
 * 执行中自动滚到最新一步，像终端输出一样；用户往上翻看时不打扰（离底部超过 80px 就不再跟随，滚回底部又恢复）。
 */
function useFollowBottom(running: boolean, content: unknown) {
  const stick = useRef(true)

  useEffect(() => {
    const onScroll = () => {
      const el = scroller()
      stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    }
    // 滚动事件不冒泡，用捕获阶段在 document 上统一监听 .app 和整页的滚动
    document.addEventListener('scroll', onScroll, true)
    return () => document.removeEventListener('scroll', onScroll, true)
  }, [])

  useEffect(() => {
    if (running && stick.current) {
      const el = scroller()
      el.scrollTop = el.scrollHeight
    }
  }, [running, content])
}
