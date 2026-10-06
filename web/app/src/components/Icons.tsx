/*
 * 图标：和设计稿一样定义成 SVG <symbol>，页面里用 <Icon name="search" /> 引用（<use href="#i-search">）。
 * 整页只放一份定义，图标颜色跟随文字颜色（stroke="currentColor"）。
 */

export function IconSprite() {
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
      <symbol id="i-search" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5 14 14" /></symbol>
      <symbol id="i-read" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="M3 2.5h7l3 3v8H3z" /><path d="M5.5 7.5h5M5.5 10h5" /></symbol>
      <symbol id="i-cite" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"><path d="M3 9.5c0-3 1.5-4.5 3.5-5M3 9.5h3V13H3zM9 9.5c0-3 1.5-4.5 3.5-5M9 9.5h3V13H9z" /></symbol>
      <symbol id="i-note" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"><path d="M10.5 2.5l3 3L6 13H3v-3z" /></symbol>
      <symbol id="i-calc" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"><rect x="3" y="2" width="10" height="12" rx="1.5" /><path d="M5.5 5h5M5.5 8.5h1M9.5 8.5h1M5.5 11.5h1M9.5 11.5h1" /></symbol>
      <symbol id="i-ok" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 8.5 6.5 12 13 4.5" /></symbol>
      <symbol id="i-chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"><path d="M6 3.5 10.5 8 6 12.5" /></symbol>
      <symbol id="i-plus" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></symbol>
      <symbol id="i-clock" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3l2 1.5" /></symbol>
      <symbol id="i-up" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></symbol>
      <symbol id="i-redo" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"><path d="M12.5 8a4.5 4.5 0 1 1-1.3-3.2M12.5 2.5v3h-3" /></symbol>
      <symbol id="i-copy" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"><rect x="5" y="5" width="8" height="8" rx="1.5" /><path d="M3 10.5V3h7.5" /></symbol>
      <symbol id="i-alert" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3.5M8 11h.01" /></symbol>
    </svg>
  )
}

export function Icon({ name, className = 'icon' }: { name: string; className?: string }) {
  return (
    <svg className={className} aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  )
}
