/*
 * 左侧栏：新提问按钮 + 历史提问（GET /runs）。
 * 简洁风下平时收成 56px 的图标栏，鼠标移上去展开（纯 CSS，见 styles/clean.css）。
 * 鼠标在历史条目之间移动时，一块底色滑到当前条目（.history-indicator），而不是每条各自变色。
 */

import { useRef } from 'react'

import type { RunStatus, RunSummary } from '../api'
import { navigate } from '../router'
import { Icon } from './Icons'

// 状态点的样式（base.css 的 .dot）：排队和执行中都显示成“正在跑”的呼吸点
const DOT: Record<RunStatus, string> = {
  queued: 'running',
  running: 'running',
  completed: 'ok',
  failed: 'failed',
}

function isToday(iso: string): boolean {
  return new Date(iso).toDateString() === new Date().toDateString()
}

interface LinkProps {
  run: RunSummary
  active: boolean
  onHover: (el: HTMLElement) => void
}

function RunLink({ run, active, onHover }: LinkProps) {
  const href = `/runs/${run.id}`
  return (
    <a
      className={active ? 'run is-active' : 'run'}
      href={href}
      onMouseEnter={(e) => onHover(e.currentTarget)}
      onClick={(e) => {
        // 拦下浏览器默认的整页跳转，改成前端路由切换；按住 Ctrl 等点击仍交给浏览器（新标签页打开）
        if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
          e.preventDefault()
          navigate(href)
        }
      }}
    >
      <i className={`dot ${DOT[run.status]}`} />
      <span className="run-text">{run.question}</span>
    </a>
  )
}

export function Sidebar({ runs, activeId }: { runs: RunSummary[]; activeId: number | null }) {
  const today = runs.filter((r) => isToday(r.created_at))
  const earlier = runs.filter((r) => !isToday(r.created_at))
  const indicator = useRef<HTMLDivElement>(null)

  // 滑块移到这一条：位置、高度跟着条目走（CSS 过渡 0.3 秒）。刚移进列表时直接到位，不从列表顶部滑下来
  function slideTo(el: HTMLElement) {
    const bar = indicator.current!
    const first = !bar.classList.contains('is-on')
    if (first) {
      bar.style.transition = 'none'
    }
    bar.style.transform = `translateY(${el.offsetTop}px)`
    bar.style.height = `${el.offsetHeight}px`
    if (first) {
      void bar.offsetWidth // 强制浏览器先应用上面的位置，再恢复过渡
      bar.style.transition = ''
    }
    bar.classList.add('is-on')
  }

  return (
    <nav className="sidebar" aria-label="历史提问">
      <div className="brand">
        <span className="brand-mark">M</span>
        <span className="brand-rest">argin</span>
      </div>
      <button className="btn-new" type="button" onClick={() => navigate('/')}>
        <Icon name="plus" />
        <span className="label">新提问</span>
      </button>
      <span className="rail-only rail-history" aria-hidden="true">
        <Icon name="clock" />
      </span>
      <div className="history" onMouseLeave={() => indicator.current!.classList.remove('is-on')}>
        <div className="history-indicator" ref={indicator} aria-hidden="true" />
        {today.length > 0 && <div className="history-label">今天</div>}
        {today.map((run) => (
          <RunLink key={run.id} run={run} active={run.id === activeId} onHover={slideTo} />
        ))}
        {earlier.length > 0 && <div className="history-label">更早</div>}
        {earlier.map((run) => (
          <RunLink key={run.id} run={run} active={run.id === activeId} onHover={slideTo} />
        ))}
      </div>
    </nav>
  )
}
