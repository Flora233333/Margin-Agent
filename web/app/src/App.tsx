/*
 * 整个应用的骨架：左侧栏（历史提问）+ 按地址显示的页面。
 *
 * 历史列表只在“可能变了”的时候重新取一次：页面打开、提交了新题、某道题执行结束（RunPage 通知）。
 * 不定时轮询——别的题的状态点要等下次刷新列表才更新，这对一个人用的历史列表足够了。
 */

import { useCallback, useEffect, useState } from 'react'
import { listRuns, type RunSummary } from './api'
import { Composer } from './components/Composer'
import { IconSprite } from './components/Icons'
import { Sidebar } from './components/Sidebar'
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

export function App() {
  const route = matchRoute(usePath())
  const [runs, setRuns] = useState<RunSummary[]>([])

  const reloadRuns = useCallback(() => {
    // 列表取失败不影响主流程（左栏暂时是旧的），只在控制台留个记录
    listRuns().then(setRuns, (e) => console.warn('历史列表加载失败', e))
  }, [])

  useEffect(reloadRuns, [reloadRuns])

  const onCreated = useCallback(
    (runId: number) => {
      navigate(`/runs/${runId}`)
      reloadRuns()
    },
    [reloadRuns],
  )

  return (
    <div className="app">
      <IconSprite />
      <Sidebar runs={runs} activeId={route.page === 'run' ? route.runId : null} />
      {route.page === 'new' && <NewRunPage onCreated={onCreated} />}
      {route.page === 'run' && <RunPage runId={route.runId} onCreated={onCreated} />}
      {route.page === 'not-found' && <NotFound />}
    </div>
  )
}
