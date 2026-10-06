/*
 * 一道题的页面：/runs/:id。
 *   1. 先用 GET /runs/{id} 取题目本身（问题、模型、提交时间）。run 不存在（或不是自己的）时后端返回 404，显示“找不到”。
 *   2. 再由 RunView 订阅 SSE（useRunStream），从 seq 0 开始收：已经发生过的事件服务端一次补发完，
 *      之后的边发生边推。所以刷新页面、从历史列表点进来、正在执行中，都是同一条代码路径。
 * 标题和答案格式（M2.5 理解题目）：执行中由 run_understood 事件送来，之前的先用 GET 的结果，
 * 都没有时标题用问题原句、格式按文本。
 */

import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import { type AnswerFormat, ApiError, type AttemptOut, getRun, regenerate, type RunDetail } from '../api'
import { citationsOf, searchedDocsOf } from '../citations'
import { Answer } from '../components/Answer'
import { Composer } from '../components/Composer'
import { Failure } from '../components/Failure'
import { Icon } from '../components/Icons'
import { Sources } from '../components/Sources'
import { Work } from '../components/Work'
import { FOLD_DELAY_MS, FOLD_MS } from '../motion'
import { type Attempt, understoodOf } from '../timeline'
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
  // 每次执行的开始 / 结束时间（账目行的“耗时”）：事件里不带时间，用 GET /runs/{id} 的 started_at / finished_at。
  // 打开页面时已经结束的执行直接有；看着它跑完的，结束时再取一次
  const [times, setTimes] = useState(run.attempts)
  const { timeline, connection, caughtUp, reopen } = useRunStream(run.id, () => {
    setActionError('') // 例如执行中点重新生成得到的 409 提示，执行结束后就过时了
    if (watching.current) {
      watching.current = false
      onRunsChanged()
      getRun(run.id).then((r) => setTimes(r.attempts), () => {}) // 取不到就不显示耗时
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
  const others = useMemo(() => searchedDocsOf(latest?.steps ?? [], citations), [latest?.steps, citations])
  const citeNos = useMemo(() => new Map(citations.map((c) => [c.stepNo, c.no])), [citations])
  useFollowBottom(latest?.status === 'running', thread)

  // 追上实时（caughtUp）的那一刻已经结束的执行：打开页面时就结束了，回答直接显示；
  // 其余的是看着它结束的，过程区停一下再收起、回答按“结论 → 正文”出场（Work.tsx、Answer.tsx）。
  // 不能用“渲染时见过它在执行”判断：打开一道已结束的题，补发的历史事件也会经过“执行中”
  const [settledEarly, setSettledEarly] = useState<Set<number> | null>(null)
  if (caughtUp && settledEarly === null) {
    const done = timeline.attempts.filter((a) => a.status === 'completed' || a.status === 'failed')
    setSettledEarly(new Set(done.map((a) => a.id)))
  }
  const watched = (attempt: Attempt) => settledEarly !== null && !settledEarly.has(attempt.id)

  const understood = understoodOf(timeline)
  const title = understood?.title ?? run.title ?? run.question
  // 给定的格式优先（评测回放）；页面上提交的题用理解题目猜的
  const format = (run.answer_format ?? understood?.answer_format ?? run.guessed_format ?? 'text') as AnswerFormat
  const label = understood?.label ?? run.answer_label
  // 打开页面时还没理解完：理解结果一到，刷新左侧列表，那里也换成标题
  const knownTitle = useRef(run.title !== null)
  useEffect(() => {
    if (understood && !knownTitle.current) {
      knownTitle.current = true
      onRunsChanged()
    }
  }, [understood, onRunsChanged])

  return (
    <>
      <main className="main">
        <header className="main-head">
          <span className="main-title">{title}</span>
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
                <Work
                  attempt={attempt}
                  watched={watched(attempt)}
                  citeNos={isLatest ? citeNos : NO_CITES}
                  understanding={isLatest && attempt.status === 'running' && !understood && run.title === null}
                />
                {attempt.status === 'failed' && <Failure error={attempt.error} />}
                {/* 撰写完（执行结束）才显示回答：先结论、再逐字出现的正文（Answer.tsx） */}
                {attempt.status === 'completed' && (
                  <Answer
                    attempt={attempt}
                    watched={watched(attempt)}
                    // 交的答案和猜的格式对不上、按文本收下了（worker.py）：这次的回答也按文本显示
                    format={attempt.final?.answer_format_fallback ? 'text' : format}
                    label={label}
                    options={run.options}
                    model={run.model}
                    seconds={secondsOf(times.find((t) => t.attempt_no === attempt.no))}
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
      <Sources
        citations={citations}
        others={others}
        live={latest?.status === 'running' || latest?.status === 'queued'}
        caughtUp={caughtUp}
        thread={thread}
        linked={linked}
        flash={flash}
        onHover={setLinked}
      />
    </>
  )
}

const NO_CITES = new Map<number, number>()

/** 一次执行从 worker 领取到结束用了多少秒；还没结束（或没取到时间）为 null */
function secondsOf(attempt: AttemptOut | undefined): number | null {
  if (!attempt?.started_at || !attempt.finished_at) {
    return null
  }
  return (Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)) / 1000
}

/** 当前负责滚动的元素：简洁风宽屏时是 .app，窄屏时 .app 不滚动，滚的是整个页面 */
function scroller(): Element {
  const app = document.querySelector('.app')!
  return getComputedStyle(app).overflowY === 'auto' ? app : document.scrollingElement!
}

/**
 * 执行中自动滚到最新一步，像终端输出一样；用户往上翻看时不打扰（离底部超过 80px 就不再跟随，滚回底部又恢复）。
 * 盯的是正文的高度（ResizeObserver），不是事件：新的一步要用 0.5 秒长出来，只在收到事件时滚一次会停在半路。
 * 执行结束时过程区自动收起、页面一下子矮了几千像素：结束后把这次执行（收起后的过程区标题 + 回答）滚到屏幕顶部，
 * 从结论开始读（用户没往上翻的话）。只滚到底部的话，回答长一点结论就在屏幕外。
 */
function useFollowBottom(running: boolean, thread: RefObject<HTMLElement | null>) {
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

  // 执行中最后一次更新时用户是否在跟随。结束那一刻过程区收起、旁注换位，页面高度一下子变了，
  // 那时的滚动位置不能说明用户的意图，所以记执行中的
  const following = useRef(false)
  useEffect(() => {
    if (!running) {
      return
    }
    const follow = () => {
      following.current = stick.current
      if (stick.current) {
        const el = scroller()
        el.scrollTop = el.scrollHeight
      }
    }
    // .thread 的高度随内容增长（.app 的行高取正文实际高度，见 app.css）；内容不满一屏时它被撑满、不变化，本来也不用滚
    const watcher = new ResizeObserver(follow)
    watcher.observe(thread.current!)
    follow()
    return () => watcher.disconnect()
  }, [running, thread])

  useEffect(() => {
    if (running || !following.current) {
      return
    }
    following.current = false
    // 等过程区停留、收起的动画播完（motion.ts），位置才是最终的
    const timer = setTimeout(() => {
      const last = [...document.querySelectorAll('.attempt')].at(-1)
      last?.querySelector('.work')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    }, FOLD_DELAY_MS + FOLD_MS + 50)
    return () => clearTimeout(timer)
  }, [running])
}
