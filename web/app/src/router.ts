/*
 * 最小的前端路由：只有三种页面（新提问 /、某道题 /runs/:id、找不到），不值得引入 react-router。
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

export function navigate(path: string): void {
  if (path !== window.location.pathname) {
    window.history.pushState(null, '', path)
    window.dispatchEvent(new Event(NAVIGATE))
  }
}

export type Route = { page: 'new' } | { page: 'run'; runId: number } | { page: 'not-found' }

export function matchRoute(path: string): Route {
  if (path === '/') {
    return { page: 'new' }
  }
  const match = /^\/runs\/(\d+)$/.exec(path)
  if (match) {
    return { page: 'run', runId: Number(match[1]) }
  }
  return { page: 'not-found' }
}
