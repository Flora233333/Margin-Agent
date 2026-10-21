/*
 * 最小的前端路由：只有四种页面（新提问 /、某道题 /runs/:id、登录 /login、找不到），不值得引入 react-router。
 *
 * 原理：history.pushState 改地址栏但不刷新页面；浏览器的前进 / 后退触发 popstate 事件。
 * usePath 订阅这两种变化，返回当前路径，组件按路径决定显示哪个页面。
 * 开发服务器（以及上线后的 Nginx）对任何路径都返回 index.html，刷新 /runs/12 才不会 404。
 */

import { useSyncExternalStore } from 'react'

const NAVIGATE = 'margin:navigate' // pushState 本身不发事件，自己补一个

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange)
  window.addEventListener(NAVIGATE, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(NAVIGATE, onChange)
  }
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, () => window.location.pathname)
}

/**
 * path 可以带查询参数（/login?next=...）；和当前地址完全相同时不动，免得历史记录里出现重复的一条。
 * replace：替换当前这条历史记录而不是新加一条。跳登录页、登录后回原页面都用它：
 * 否则登录后点“后退”会回到登录页，再后退又被踢回登录页。
 */
export function navigate(path: string, { replace = false } = {}): void {
  if (path !== window.location.pathname + window.location.search) {
    if (replace) {
      window.history.replaceState(null, '', path)
    } else {
      window.history.pushState(null, '', path)
    }
    window.dispatchEvent(new Event(NAVIGATE))
  }
}

export type Route = { page: 'new' } | { page: 'run'; runId: number } | { page: 'login' } | { page: 'not-found' }

export function matchRoute(path: string): Route {
  if (path === '/') {
    return { page: 'new' }
  }
  if (path === '/login') {
    return { page: 'login' }
  }
  const match = /^\/runs\/(\d+)$/.exec(path)
  if (match) {
    return { page: 'run', runId: Number(match[1]) }
  }
  return { page: 'not-found' }
}
