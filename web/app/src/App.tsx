/*
 * 整个应用的骨架：左侧栏（历史提问、账号）+ 按地址显示的页面；没登录时只有登录页。
 *
 * 登录（M3a）：
 *   - 打开页面先问一次 GET /auth/me“我是谁”，问到之前什么都不画（不然没登录的人会先看到页面闪一下再跳走）；
 *   - 任何请求收到 401 → 跳登录页，带上现在的地址（登录后回来）。之前是登录着的，就是“登录过期”，登录页顶上说一声；
 *   - 登录成功回到原来的页面；退出后回登录页。换了人，历史列表清空重取，不会看到上一个人的题。
 *
 * 历史列表只在“可能变了”的时候重新取一次：登录后、提交了新题、某道题执行结束（RunPage 通知）。
 * 不定时轮询——别的题的状态点要等下次刷新列表才更新，这对一个人用的历史列表足够了。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getMe, listRuns, logout, type RunSummary, setUnauthorizedHandler } from './api'
import { LOGIN_PATH, loginPath, safeNext } from './auth'
import { Composer } from './components/Composer'
import { IconSprite } from './components/Icons'
import { Sidebar } from './components/Sidebar'
import { LoginPage } from './pages/LoginPage'
import { NotFound } from './pages/NotFound'
import { RunPage } from './pages/RunPage'
import { matchRoute, navigate, usePath } from './router'

function NewRunPage({ onCreated }: { onCreated: (runId: number) => void }) {
  return (
    <main className="main">
      <div className="thread">
        <div className="empty">
          <h1 className="empty-title">问一个关于债券募集说明书的问题</h1>
          <p>Margin 会检索原文、逐步核对，并给出可以点回原文的引用。</p>
        </div>
      </div>
      <Composer onCreated={onCreated} />
    </main>
  )
}

// 当前用户：undefined = 正在问后端；null = 没登录；字符串 = 用户名
// （空串 = 打开页面时 API 连不上、不知道是谁：照常显示页面，各个请求自己报“连不上服务器”）
type Who = undefined | null | string

export function App() {
  const route = matchRoute(usePath())
  const [who, setWho] = useState<Who>()
  const [runs, setRuns] = useState<RunSummary[]>([])
  // 401 的处理函数只在启动时设置一次，要从它里面读“最新的”当前用户，所以另存一份在 ref 里
  const whoRef = useRef<Who>(undefined)
  const changeWho = useCallback((next: Who) => {
    whoRef.current = next
    setWho(next)
  }, [])

  const reloadRuns = useCallback(() => {
    // 列表取失败不影响主流程（左栏暂时是旧的），只在控制台留个记录
    listRuns().then(setRuns, (e) => console.warn('历史列表加载失败', e))
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(() => {
      const expired = !!whoRef.current // 之前确实登录着（知道用户名）= 这次是过期了
      changeWho(null)
      setRuns([])
      // 已经在登录页（登录页自己打开时问 /auth/me 也会 401）就不动，免得把地址里的 next 弄丢
      if (window.location.pathname !== LOGIN_PATH) {
        navigate(loginPath(window.location.pathname, expired), { replace: true })
      }
    })
    getMe().then(
      (me) => {
        changeWho(me.username)
        reloadRuns()
      },
      (e) => e.status !== 401 && changeWho(''), // 401 已经由上面的处理函数接手
    )
  }, [changeWho, reloadRuns])

  const onLoggedIn = useCallback(
    (username: string) => {
      changeWho(username)
      setRuns([])
      reloadRuns()
      navigate(safeNext(new URLSearchParams(window.location.search).get('next')), { replace: true })
    },
    [changeWho, reloadRuns],
  )

  const onLogout = useCallback(() => {
    // 退出请求失败（断网）也照样回登录页：本地当作已退出，cookie 留在浏览器里，下次退出或过期时清掉
    void logout()
      .catch(() => {})
      .finally(() => {
        changeWho(null)
        setRuns([])
        navigate(LOGIN_PATH, { replace: true })
      })
  }, [changeWho])

  const onCreated = useCallback(
    (runId: number) => {
      navigate(`/runs/${runId}`)
      reloadRuns()
    },
    [reloadRuns],
  )

  if (route.page === 'login') {
    return (
      <>
        <IconSprite />
        <LoginPage onLoggedIn={onLoggedIn} />
      </>
    )
  }
  if (who === undefined || who === null) {
    return null // 正在确认是谁，或者没登录、正在跳登录页
  }
  return (
    <div className="app">
      <IconSprite />
      <Sidebar runs={runs} activeId={route.page === 'run' ? route.runId : null} username={who} onLogout={onLogout} />
      {route.page === 'new' && <NewRunPage onCreated={onCreated} />}
      {route.page === 'run' && (
        // key：换一道题时 RunPage 整个重建，题目、事件流（useRunStream）、“加载中…”的计时都从头开始
        <RunPage key={route.runId} runId={route.runId} onCreated={onCreated} onRunsChanged={reloadRuns} />
      )}
      {route.page === 'not-found' && <NotFound />}
    </div>
  )
}
