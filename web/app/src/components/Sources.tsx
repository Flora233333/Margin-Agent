/*
 * 右侧来源：每条引用一张卡片（文档、位置、原文里标出引文）。
 *
 * 简洁风宽屏时，右栏有两种排法（useMarginLayout）：
 *   执行中  顺序列表，整栏“吸”在屏幕顶部（.panel-body.is-live，CSS sticky）。页面一直在往下滚、过程区一直在变长，
 *           卡片如果去对齐过程区里的步骤，会跟着滚出屏幕、被后来的卡片推开，看起来在乱飘（M2 逐帧截图 run 32）；
 *   结束后  页边旁注（D14）：每张卡片和回答里的 [n] 对齐，互不重叠。过程区展开时也不去追里面的步骤，
 *           卡片只跟着回答走，展开 / 收起过程区时和回答一起平移。
 * 窄屏（≤1180px）时 CSS 把卡片变回普通列表，这里清掉 top。
 * 执行中每出现一张新卡片，从过程区里引用它的那一步画一条线过去，1.5 秒后收回（useAnnounce），表示“这条证据从这里来”。
 * 算法来自设计稿 web/design/demo.js 第 4 部分。
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

/** 结束后卡片对齐的位置：回答里的 [n]。执行失败、没有回答时没有锚点，排成列表 */
function anchorOf(no: string): Element | null {
  const el = document.querySelector(`.answer .cite[data-src="${no}"]`)
  return el && isShown(el) ? el : null
}

/*
 * 下面两个函数算“动画播完以后”的位置和高度。
 * 过程区展开 / 收起、原文展开都要播 0.5 秒动画；如果每一帧按当时量到的位置去追，目标一直在变，
 * 卡片会一顿一顿地落在后面（逐帧截图里晚了约 250ms）。所以动画一开始就算出终点，卡片直接滑过去，和内容同时到达。
 */

/** 锚点上方各个过程区还要变化的高度之和（展开为正，收起为负，静止时为 0） */
function workShift(anchor: Element): number {
  let shift = 0
  for (const wrap of document.querySelectorAll('.work-body-wrap')) {
    if (wrap.compareDocumentPosition(anchor) & Node.DOCUMENT_POSITION_FOLLOWING) {
      const open = wrap.closest('.work')!.classList.contains('is-open')
      const finalHeight = open ? wrap.firstElementChild!.scrollHeight : 0
      shift += finalHeight - wrap.getBoundingClientRect().height
    }
  }
  return shift
}

/** 一张卡片动画播完后的高度：内容的完整高度，原文区换成最终高度（展开 = 全文，收起 = 3 行） */
function finalItemHeight(item: HTMLElement): number {
  let height = item.querySelector('.reveal-inner')!.scrollHeight
  const text = item.querySelector<HTMLElement>('.source-text')!
  const threeLines = parseFloat(getComputedStyle(text).fontSize) * 1.7 * 3 // 与 base.css 的 max-height 一致
  const finalText = text.closest('.is-expanded') ? text.scrollHeight : Math.min(text.scrollHeight, threeLines)
  height += finalText - text.getBoundingClientRect().height
  return height
}

function useMarginLayout(track: RefObject<HTMLDivElement | null>, thread: RefObject<HTMLElement | null>,
  count: number, live: boolean) {
  useLayoutEffect(() => {
    const trackEl = track.current!
    const body = trackEl.parentElement! // .panel-body：执行中整栏吸顶
    const items = [...trackEl.querySelectorAll<HTMLElement>('.margin-item')]

    /*
     * 切换“执行中 / 结束后”时，整栏从吸顶的位置回到页面里的位置，轨道一下子跳到别处。
     * 先记下每张卡片此刻在屏幕上的位置，切换后把它们原地放回那里（不播过渡），再滑向新位置——
     * 这个技巧叫 FLIP（First 记下起点、Last 算终点、Invert 放回起点、Play 播放）。
     */
    function switchMode() {
      const before = items.map((item) => item.getBoundingClientRect().top)
      body.classList.toggle('is-live', live)
      const trackTop = trackEl.getBoundingClientRect().top
      items.forEach((item, i) => {
        item.classList.add('is-placing')
        item.style.top = `${before[i] - trackTop}px`
      })
      void trackEl.offsetWidth // 强制浏览器先应用上面的位置，再恢复过渡
      items.forEach((item) => item.classList.remove('is-placing'))
    }

    function layout() {
      if (!matchMedia(WIDE).matches) {
        body.classList.remove('is-live')
        items.forEach((item) => (item.style.top = ''))
        trackEl.style.height = ''
        return
      }
      if (body.classList.contains('is-live') !== live) {
        switchMode()
      }
      const trackTop = trackEl.getBoundingClientRect().top
      // 每张卡片的目标位置：结束后是 [n] 的高度，执行中（或没有锚点）是 0，从上往下排，会压住上一张就往下推
      const placed = items.map((item) => {
        const anchor = live ? null : anchorOf(item.dataset.src!)
        const target = anchor ? anchor.getBoundingClientRect().top - trackTop - 6 + workShift(anchor) : 0
        return { item, target }
      })
      placed.sort((a, b) => a.target - b.target) // 稳定排序：目标相同的按引用编号
      let floor = 0
      for (const { item, target } of placed) {
        const top = Math.max(target, floor)
        if (!item.dataset.placed) {
          // 第一次出现：直接放到位（暂时关掉 top 的过渡），不从轨道顶部滑下来
          item.classList.add('is-placing')
          item.style.top = `${top}px`
          void item.offsetWidth
          item.classList.remove('is-placing')
          item.dataset.placed = '1'
        } else {
          item.style.top = `${top}px`
        }
        floor = top + finalItemHeight(item) + GAP
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
    // 过程区展开 / 收起的那一刻（类名变化）就要算终点，不等它的高度开始变
    const toggled = new MutationObserver(schedule)
    toggled.observe(threadEl, { subtree: true, attributes: true, attributeFilter: ['class'] })
    window.addEventListener('resize', schedule)
    schedule()
    return () => {
      cancelAnimationFrame(frame)
      watcher.disconnect()
      added.disconnect()
      toggled.disconnect()
      window.removeEventListener('resize', schedule)
    }
  }, [track, thread, count, live])
}

const ANNOUNCE_DELAY = 550 // 等卡片入场动画（0.5 秒）基本播完再画线，线的终点才是卡片的最终位置
const ANNOUNCE_MS = 1500 // 线停留多久后收回

/**
 * 执行中新卡片出现时画一条连线：起点是过程区里“引用”那一步的标题行（线从过程区右边缘出发，只穿过空白，不压在文字上），
 * 终点是卡片左上。线画在 .app 的内容坐标里（.app 是滚动容器），用户这时滚动页面，线跟着内容走。
 * pathLength=1 + stroke-dashoffset 从 1 过渡到 0，线像被画出来（clean.css 的 .connector）。
 */
function useAnnounce(path: RefObject<SVGPathElement | null>, count: number, live: boolean) {
  const seen = useRef(count) // 打开页面时已经有的卡片不画
  useEffect(() => {
    const before = seen.current
    seen.current = count
    if (!live || count <= before || !matchMedia(WIDE).matches) {
      return
    }
    const line = path.current!
    const no = String(count) // 编号按出现顺序，最新的卡片就是编号最大的那张
    const draw = setTimeout(() => {
      const step = document.querySelector(`.step[data-cite="${no}"]`)
      const card = document.querySelector(`.source[data-src="${no}"]`)
      if (!step || !isShown(step) || !card) {
        return // 用户把过程区收起来了：起点看不见，就不画
      }
      const app = document.querySelector('.app')!
      const base = app.getBoundingClientRect()
      const a = (step.querySelector('.step-row') ?? step).getBoundingClientRect()
      const b = card.getBoundingClientRect()
      const x1 = step.closest('.work')!.getBoundingClientRect().right - base.left + 12
      const y1 = a.top + a.height / 2 - base.top + app.scrollTop
      const x2 = b.left - base.left - 4
      const y2 = b.top + 18 - base.top + app.scrollTop
      const mid = (x1 + x2) / 2
      line.parentElement!.style.height = `${app.scrollHeight}px`
      // 先在“无过渡”状态下把线收回到长度 0，再恢复过渡画出；否则上一条线还没收完时，新线会整条直接出现
      line.style.transition = 'none'
      line.classList.remove('is-on')
      line.setAttribute('d', `M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`)
      void line.getBoundingClientRect()
      line.style.transition = ''
      line.classList.add('is-on')
    }, ANNOUNCE_DELAY)
    const hide = setTimeout(() => line.classList.remove('is-on'), ANNOUNCE_DELAY + ANNOUNCE_MS)
    return () => {
      // 1.5 秒内又来了新卡片、或执行结束：收回这条线，由下一次重新画
      clearTimeout(draw)
      clearTimeout(hide)
      line.classList.remove('is-on')
    }
  }, [path, count, live])
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
  const text = useRef<HTMLDivElement>(null)

  // 原文展开的目标高度 = 原文实际高度（scrollHeight）。CSS 的 max-height 过渡需要具体的数，
  // 写一个“足够大”的值（之前是 40em）的话，短原文会在动画开头几十毫秒内就展开完，看起来是跳的
  useLayoutEffect(() => {
    const el = text.current!
    el.style.maxHeight = expanded ? `${el.scrollHeight}px` : ''
  }, [expanded])

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
      {/* 收起时只露 3 行（base.css 的 max-height），展开时的高度由上面的 effect 量出来 */}
      <div className="source-text" ref={text}>
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
  live: boolean // 最近一次执行还没结束
  thread: RefObject<HTMLElement | null>
  linked: number | null
  flash: { no: number; count: number }
  onHover: (no: number | null) => void
}

export function Sources({ citations, live, thread, linked, flash, onHover }: Props) {
  const track = useRef<HTMLDivElement>(null)
  const line = useRef<SVGPathElement>(null)
  useMarginLayout(track, thread, citations.length, live)
  useAnnounce(line, citations.length, live)
  return (
    <>
      <aside className="side-panel" aria-label="来源">
        {/* is-live 类由 useMarginLayout 切换（要和卡片位置的调整在同一时刻发生），这里不写 */}
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
      {/* 连线画在整个 .app 上（绝对定位，跨过正文和右栏），只在简洁风宽屏显示 */}
      <svg className="connector" aria-hidden="true">
        <path pathLength={1} ref={line} />
      </svg>
    </>
  )
}
