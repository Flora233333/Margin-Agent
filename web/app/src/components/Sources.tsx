/*
 * 右侧来源：每条引用一张卡片（文档、位置、原文里标出引文）。
 *
 * 简洁风的“页边旁注”（D14）：宽屏时每张卡片和引用它的地方对齐——过程区里的“引用”那一步，或回答里的 [n]。
 * 卡片在轨道里绝对定位，top 由 useMarginLayout 算；窄屏（≤1180px）时 CSS 把它们变回普通列表，这里也清掉 top。
 * 算法照搬设计稿 web/design/demo.js 第 4 部分，去掉了“提前算动画终点”的部分（见 M2 导读）。
 */

import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Citation } from '../citations'
import { Icon } from './Icons'

const GAP = 16 // 两张卡片之间至少隔开的距离
const WIDE = '(min-width: 1181px)' // 与 clean.css 的窄屏断点一致

/** 元素此刻是否看得见：不在收起的过程区里，并且有尺寸 */
function isShown(el: Element): boolean {
  return !el.closest('.work:not(.is-open) .work-body-wrap') && el.getClientRects().length > 0
}

/** 卡片要对齐的位置：过程区展开时对齐到里面的“引用”那一步（正在看的地方），收起后对齐到回答里的 [n] */
function anchorOf(no: string): Element | null {
  for (const selector of [`.step[data-cite="${no}"]`, `.answer .cite[data-src="${no}"]`]) {
    const el = document.querySelector(selector)
    if (el && isShown(el)) {
      return el
    }
  }
  return null
}

function useMarginLayout(track: RefObject<HTMLDivElement | null>, thread: RefObject<HTMLElement | null>,
  count: number) {
  useLayoutEffect(() => {
    const trackEl = track.current!
    const items = [...trackEl.querySelectorAll<HTMLElement>('.margin-item')]

    function layout() {
      if (!matchMedia(WIDE).matches) {
        items.forEach((item) => (item.style.top = ''))
        trackEl.style.height = ''
        return
      }
      const trackTop = trackEl.getBoundingClientRect().top
      // 每张卡片的目标位置 = 锚点的高度；从上往下排，会压住上一张就往下推
      const placed = items.map((item, i) => {
        const anchor = anchorOf(item.dataset.src!)
        return { item, target: anchor ? anchor.getBoundingClientRect().top - trackTop - 6 : i * 120 }
      })
      placed.sort((a, b) => a.target - b.target)
      let floor = 0
      for (const { item, target } of placed) {
        const top = Math.max(target, floor)
        if (!item.dataset.placed) {
          // 第一次出现：直接放到位（暂时关掉 top 的过渡），不从轨道顶部滑下来
          item.classList.add('is-placing')
          item.style.top = `${top}px`
          void item.offsetWidth // 强制浏览器先应用上面的位置，再恢复过渡
          item.classList.remove('is-placing')
          item.dataset.placed = '1'
        } else {
          item.style.top = `${top}px`
        }
        floor = top + item.offsetHeight + GAP
      }
      trackEl.style.height = `${floor}px` // 卡片都是绝对定位，不占高度，要自己把轨道撑开
    }

    // 同一帧里多次触发只算一次
    let frame = 0
    const schedule = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(layout)
    }
    // 正文（过程区展开收起、逐字输出、回答出现）或卡片自己（展开原文）高度变了，都要重新对齐。
    // 盯的是正文的各个子块，不是正文整体：轨道被撑高后整页一起变高，正文被拉伸，它自己的高度就不再变化了。
    // 重新生成会在正文里加一块新的，所以再用 MutationObserver 盯住“子元素增加”
    const threadEl = thread.current!
    const watcher = new ResizeObserver(schedule)
    const watchChildren = () => [...threadEl.children].forEach((child) => watcher.observe(child))
    const added = new MutationObserver(() => {
      watchChildren()
      schedule()
    })
    watchChildren()
    items.forEach((item) => watcher.observe(item))
    added.observe(threadEl, { childList: true })
    window.addEventListener('resize', schedule)
    schedule()
    return () => {
      cancelAnimationFrame(frame)
      watcher.disconnect()
      added.disconnect()
      window.removeEventListener('resize', schedule)
    }
  }, [track, thread, count])
}

interface CardProps {
  citation: Citation
  linked: boolean
  flash: number // 每次点 [n] 加 1，卡片据此闪一下并展开
  onHover: (no: number | null) => void
}

function SourceCard({ citation, linked, flash, onHover }: CardProps) {
  // 用户最后一次手动展开 / 收起，以及当时的 flash 值。之后又点了 [n]（flash 变大）就展开，否则按用户的来
  const [toggled, setToggled] = useState({ open: false, flash: 0 })
  const expanded = flash > toggled.flash || toggled.open
  const card = useRef<HTMLElement>(null)

  useEffect(() => {
    if (flash === 0) {
      return
    }
    // 重新触发 CSS 动画：先去掉类，强制浏览器读一次布局，再加回去
    const el = card.current!
    el.classList.remove('is-flash')
    void el.offsetWidth
    el.classList.add('is-flash')
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [flash])

  const classes = ['source', expanded && 'is-expanded', linked && 'is-linked'].filter(Boolean).join(' ')
  return (
    <article
      ref={card}
      className={classes}
      id={`src-${citation.no}`}
      data-src={citation.no}
      onMouseEnter={() => onHover(citation.no)}
      onMouseLeave={() => onHover(null)}
    >
      <button className="source-head" type="button" aria-expanded={expanded} onClick={() => setToggled({ open: !expanded, flash })}>
        <span className="source-no">{citation.no}</span>
        <span className="source-doc">{citation.docId}</span>
        <Icon name="chev" className="source-chev" />
      </button>
      <div className="source-loc">
        {citation.blockId} · 第 {citation.stepNo} 步引用
      </div>
      {/* 收起时只露 3 行（base.css 的 max-height），展开见 app.css */}
      <div className="source-text">
        <p className="source-line">
          {citation.before}
          <mark>{citation.match}</mark>
          {citation.after}
        </p>
      </div>
    </article>
  )
}

interface Props {
  citations: Citation[]
  thread: RefObject<HTMLElement | null>
  linked: number | null
  flash: { no: number; count: number }
  onHover: (no: number | null) => void
}

export function Sources({ citations, thread, linked, flash, onHover }: Props) {
  const track = useRef<HTMLDivElement>(null)
  useMarginLayout(track, thread, citations.length)
  return (
    <aside className="side-panel" aria-label="来源">
      <div className="panel-body">
        <div className="panel-head">
          <h2 className="panel-title">
            来源 <span className="num">{citations.length}</span>
          </h2>
        </div>
        <div className="margin-track" ref={track}>
          {citations.length === 0 && <p className="sources-empty">模型引用原文后，来源会出现在这里。</p>}
          {citations.map((c) => (
            <div className="reveal margin-item" key={c.no} data-src={c.no}>
              <div className="reveal-inner">
                <SourceCard
                  citation={c}
                  linked={linked === c.no}
                  flash={flash.no === c.no ? flash.count : 0}
                  onHover={onHover}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </aside>
  )
}
