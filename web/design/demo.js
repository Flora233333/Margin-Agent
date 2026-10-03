/*
 * 设计稿的回放与交互脚本：只用来演示动效，不是产品代码。
 *
 * 分六部分：
 *   1. 回放：按 data-seq 编号依次展开各元素，逐字输出，数字滚动，过程区标题切换
 *   2. 过程区折叠
 *   3. 来源：整栏收起 / 展开、单条原文展开、点引用跳到来源、悬停联动
 *   4. 简洁风的页边旁注：与引用对齐、互不重叠、悬停连线
 *   5. 小交互：历史悬停滑块、复制按钮
 *   6. 设计评审面板：风格 / 字体 / 配色 / 深浅，偏好存在浏览器本地
 *
 * 回放用到的 HTML 属性：
 *   data-seq="N"  编号相同的元素同时展开（元素本身是 .reveal 展开容器）
 *   data-wait     展开前等待的毫秒数（默认 700）
 *   data-label    展开时过程区标题显示的文字
 *   data-finish   展开前先结束过程区（折叠成一行摘要）
 *   data-stream   逐字输出的段落
 *   data-count    展开时从 0 滚动到这个数
 */

const root = document.documentElement;
const app = document.querySelector(".app");
const main = document.querySelector(".main");
const work = document.querySelector(".work");
const workHead = work.querySelector(".work-head");
const workLabel = work.querySelector(".work-label");
const sendButton = document.querySelector(".send");
const marginTrack = document.querySelector(".margin-track");
const marginItems = [...document.querySelectorAll(".margin-item")];
const connector = document.querySelector(".connector");
const connectorPath = connector.querySelector("path");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* 重新触发一个 CSS 动画：先去掉类，强制浏览器读一次布局，再加回去 */
function replayClass(el, className, duration) {
  el.classList.remove(className);
  void el.offsetWidth;
  el.classList.add(className);
  setTimeout(() => el.classList.remove(className), duration);
}

/* ======================================== 1. 回放 ======================================== */

// 按编号分组
const items = [...document.querySelectorAll("[data-seq]")];
const groups = new Map();
for (const el of items) {
  const n = Number(el.dataset.seq);
  if (!groups.has(n)) groups.set(n, []);
  groups.get(n).push(el);
}
const order = [...groups.keys()].sort((a, b) => a - b);

// 记下要逐字输出的段落的原始内容（文字 + <strong>、引用编号等元素）
const streams = [...document.querySelectorAll("[data-stream]")];
const originals = new Map();
for (const el of streams) originals.set(el, [...el.childNodes].map((node) => node.cloneNode(true)));

let runId = 0;   // 每次回放加 1；旧的一轮发现编号变了就停下

/* 逐字输出：文字每次 2 个字，元素（如引用编号）整体出现，保证 [1] 不会被拆开 */
async function stream(el, id) {
  // 当前风格下不显示的段落（例如简洁风隐藏了第一句），直接放回原文，不占用时间
  if (el.offsetParent === null) {
    el.replaceChildren(...originals.get(el).map((node) => node.cloneNode(true)));
    return;
  }
  for (const node of originals.get(el)) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent;
      for (let i = 0; i < text.length; i += 2) {
        const span = document.createElement("span");
        span.className = "tok";
        span.textContent = text.slice(i, i + 2);
        el.append(span);
        await sleep(26);
        if (id !== runId) return;
      }
    } else {
      const clone = node.cloneNode(true);
      clone.classList.add("tok");
      el.append(clone);
      scheduleLayout();   // 引用编号出现了，旁注要移过来对齐
      await sleep(80);
      if (id !== runId) return;
    }
  }
}

/* 数字从 0 滚动到目标值，先快后慢；小数位数与目标值相同 */
function countUp(el) {
  const target = Number(el.dataset.count);
  const decimals = (el.dataset.count.split(".")[1] ?? "").length;
  const start = performance.now();
  function frame(now) {
    const t = Math.min(1, (now - start) / 800);
    const eased = 1 - (1 - t) ** 3;
    el.textContent = (target * eased).toFixed(decimals);
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* 过程区标题：新文字从下方淡入，旧文字上移淡出后删除 */
function setLabel(text) {
  const old = workLabel.querySelector("span:not(.is-leaving)");
  if (old && old.textContent === text) return;
  const next = document.createElement("span");
  next.textContent = text;
  next.className = "is-entering";
  next.addEventListener("animationend", () => next.classList.remove("is-entering"), { once: true });
  if (old) {
    old.className = "is-leaving";
    old.addEventListener("animationend", () => old.remove(), { once: true });
  }
  workLabel.append(next);
}

function streamsIn(el) {
  return el.matches("[data-stream]") ? [el] : [...el.querySelectorAll("[data-stream]")];
}

/* 找到当前负责滚动的元素：简洁风是整页 .app，其他风格是中栏 .main；窄屏时是整个文档 */
function scroller() {
  return [main, app].find((el) => getComputedStyle(el).overflowY === "auto");
}

function followBottom() {
  const s = scroller();
  s?.scrollTo({ top: s.scrollHeight, behavior: "smooth" });
}

function markCurrent(step) {
  for (const s of document.querySelectorAll(".step.is-current")) s.classList.remove("is-current");
  step?.classList.add("is-current");
}

function setWorkOpen(open) {
  work.classList.toggle("is-open", open);
  workHead.setAttribute("aria-expanded", String(open));
}

function resetAll() {
  root.classList.add("no-transition");
  for (const el of items) el.classList.add("is-hidden");
  for (const el of streams) el.replaceChildren();
  for (const el of marginItems) delete el.dataset.placed;
  work.classList.add("is-running");
  workLabel.replaceChildren(Object.assign(document.createElement("span"), { textContent: "正在思考" }));
  sendButton.classList.add("is-swapped");   // 运行中：发送箭头变成“停止”方块
  setWorkOpen(true);
  markCurrent(null);
  scroller()?.scrollTo({ top: 0 });
  // 读一次布局，强制浏览器先应用“收起”状态；否则下面去掉 is-hidden 时不会有过渡
  void root.offsetHeight;
  root.classList.remove("no-transition");
}

async function finishWork(id) {
  markCurrent(null);
  work.classList.remove("is-running");
  setLabel(work.dataset.doneLabel);
  await sleep(700);
  if (id !== runId) return;
  setWorkOpen(false);
  await sleep(450);
}

async function play() {
  const id = ++runId;
  resetAll();
  for (const n of order) {
    const group = groups.get(n);
    const first = group[0];
    await sleep(Number(first.dataset.wait ?? 700));
    if (id !== runId) return;

    if ("finish" in first.dataset) {
      await finishWork(id);
      if (id !== runId) return;
    }
    if (first.dataset.label) setLabel(first.dataset.label);

    for (const el of group) {
      el.classList.remove("is-hidden");
      if (el.classList.contains("step")) markCurrent(el);
      for (const counter of el.querySelectorAll("[data-count]")) countUp(counter);
    }
    scheduleLayout();
    followBottom();

    for (const el of group) {
      for (const s of streamsIn(el)) {
        await stream(s, id);
        if (id !== runId) return;
        followBottom();
      }
    }
  }
  sendButton.classList.remove("is-swapped");
}

/* ======================================== 2. 过程区折叠 ======================================== */
workHead.addEventListener("click", () => setWorkOpen(!work.classList.contains("is-open")));

/* ======================================== 3. 来源 ======================================== */
const panelToggle = document.querySelector(".panel-toggle");

function setSourcesOpen(open) {
  app.classList.toggle("is-sources-closed", !open);
  panelToggle.setAttribute("aria-expanded", String(open));
  hideConnector();
  setTimeout(scheduleLayout, 460);   // 列宽过渡结束后，正文换行变了，旁注重新对齐
}
panelToggle.addEventListener("click", () => setSourcesOpen(false));
document.querySelector(".panel-tab").addEventListener("click", () => setSourcesOpen(true));

/* 单条来源展开：目标高度 = 原文实际高度（scrollHeight），收起时回到 CSS 里的 3 行 */
function setSourceExpanded(card, open) {
  const text = card.querySelector(".source-text");
  card.classList.toggle("is-expanded", open);
  card.querySelector(".source-head").setAttribute("aria-expanded", String(open));
  text.style.maxHeight = open ? `${text.scrollHeight}px` : "";
}
for (const card of document.querySelectorAll(".source")) {
  card.querySelector(".source-head").addEventListener("click", () => {
    const open = !card.classList.contains("is-expanded");
    setSourceExpanded(card, open);
    if (open) replayClass(card, "is-flash", 1300);
  });
}

/* 点正文里的引用编号：展开来源栏 → 滚动到对应来源 → 展开它并闪一下 */
document.addEventListener("click", (event) => {
  const cite = event.target.closest(".answer .cite");
  if (!cite) return;
  event.preventDefault();
  const card = document.querySelector(`.source[data-src="${cite.dataset.src}"]`);
  setSourcesOpen(true);
  setSourceExpanded(card, true);
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  replayClass(card, "is-flash", 1300);
});

/* 可折叠小组（检索命中的其他文档） */
for (const head of document.querySelectorAll(".group-head")) {
  head.addEventListener("click", () => {
    const open = head.getAttribute("aria-expanded") !== "true";
    head.setAttribute("aria-expanded", String(open));
    head.nextElementSibling.classList.toggle("is-closed", !open);
  });
}

/* 鼠标移到引用编号或来源卡片上：两边同时高亮，简洁风里再画一条连线 */
function linkSources(event, on) {
  const el = event.target.closest("[data-src]");
  if (!el) return;
  for (const x of document.querySelectorAll(`[data-src="${el.dataset.src}"]`)) {
    x.classList.toggle("is-linked", on);
  }
  if (on) showConnector(el.dataset.src);
  else hideConnector();
}
document.addEventListener("mouseover", (event) => linkSources(event, true));
document.addEventListener("mouseout", (event) => linkSources(event, false));

/* ======================================== 4. 简洁风：页边旁注 ======================================== */

/* 只有简洁风、宽屏、来源栏展开时才做对齐；其他情况下旁注就是普通列表 */
function marginEnabled() {
  return root.dataset.style === "clean"
    && matchMedia("(min-width: 1181px)").matches
    && !app.classList.contains("is-sources-closed");
}

/* 元素此刻是否真的看得见：不在收起的容器里，并且当前风格下有尺寸 */
function isShown(el) {
  return !el.closest(".is-hidden, .work:not(.is-open) .work-body-wrap") && el.getClientRects().length > 0;
}

/* data-anchor 里按顺序列出候选元素，取第一个看得见的 */
function anchorOf(item) {
  for (const selector of item.dataset.anchor.split("|")) {
    const el = document.querySelector(selector);
    if (el && isShown(el)) return el;
  }
  return null;
}

/*
 * 旁注对齐：每条旁注的目标位置 = 它的锚点（正文里的引用，或过程区里的引用步骤）所在高度；
 * 从上到下排，如果会压住上一条，就往下推。位置变化时 CSS 让 top 平滑过渡，旁注会“滑”到新位置。
 */
function layoutMargin() {
  if (!marginEnabled()) {
    for (const item of marginItems) item.style.top = "";
    marginTrack.style.height = "";
    return;
  }
  const trackTop = marginTrack.getBoundingClientRect().top;
  const placed = [];
  for (const item of marginItems) {
    if (item.classList.contains("is-hidden")) continue;
    const anchor = anchorOf(item);
    const target = anchor ? anchor.getBoundingClientRect().top - trackTop - 6 : item.offsetTop;
    placed.push({ item, target });
  }
  placed.sort((a, b) => a.target - b.target);

  let floor = 0;
  for (const { item, target } of placed) {
    const top = Math.max(target, floor);
    if (!item.dataset.placed) {
      // 第一次出现：直接放到位，不从顶部滑下来
      item.style.transition = "none";
      item.style.top = `${top}px`;
      void item.offsetWidth;
      item.style.transition = "";
      item.dataset.placed = "1";
    } else {
      item.style.top = `${top}px`;
    }
    floor = top + item.offsetHeight + 16;
  }
  marginTrack.style.height = `${floor}px`;
}

// 同一帧里多次请求只算一次
let layoutQueued = false;
function scheduleLayout() {
  if (layoutQueued) return;
  layoutQueued = true;
  requestAnimationFrame(() => {
    layoutQueued = false;
    layoutMargin();
  });
}
// 正文高度变化（展开动画、逐字输出、换行）或旁注高度变化（原文展开）时重新对齐
const resizeWatcher = new ResizeObserver(scheduleLayout);
resizeWatcher.observe(document.querySelector(".thread"));
for (const item of marginItems) resizeWatcher.observe(item);
window.addEventListener("resize", scheduleLayout);

/*
 * 连线：从引用编号所在那一行的正文右边缘，画一条曲线到旁注左边；用 dashoffset 做“画出来”的效果。
 * 起点放在正文右边缘而不是引用编号本身，线就只穿过空白，不会像删除线一样压在文字上。
 */
function showConnector(src) {
  if (!marginEnabled()) return;
  const cite = document.querySelector(`.answer .cite[data-src="${src}"]`);
  const card = document.querySelector(`.source[data-src="${src}"]`);
  if (!cite || !isShown(cite) || !isShown(card)) return;

  // 坐标换算成 .app 内容里的位置（.app 是滚动容器，连线跟着内容一起滚动）
  const base = app.getBoundingClientRect();
  const a = cite.getBoundingClientRect();
  const b = card.getBoundingClientRect();
  const textRight = cite.closest(".answer").getBoundingClientRect().right;
  const x1 = textRight - base.left + 12;
  const y1 = a.top + a.height / 2 - base.top + app.scrollTop;
  const x2 = b.left - base.left - 4;
  const y2 = b.top + 18 - base.top + app.scrollTop;
  const mid = (x1 + x2) / 2;

  connector.style.height = `${app.scrollHeight}px`;
  connectorPath.classList.remove("is-on");
  connectorPath.setAttribute("d", `M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
  void connectorPath.getBoundingClientRect();
  connectorPath.classList.add("is-on");
}
function hideConnector() {
  connectorPath.classList.remove("is-on");
}

/* ======================================== 5. 小交互 ======================================== */

/* 历史列表悬停滑块：一块底色滑到鼠标所在条目；第一次出现时直接到位 */
const historyList = document.querySelector(".history");
const indicator = historyList.querySelector(".history-indicator");
for (const run of historyList.querySelectorAll(".run")) {
  run.addEventListener("mouseenter", () => {
    const firstShow = !indicator.classList.contains("is-on");
    if (firstShow) indicator.style.transition = "none";
    indicator.style.transform = `translateY(${run.offsetTop}px)`;
    indicator.style.height = `${run.offsetHeight}px`;
    if (firstShow) {
      void indicator.offsetWidth;
      indicator.style.transition = "";
    }
    indicator.classList.add("is-on");
  });
}
historyList.addEventListener("mouseleave", () => indicator.classList.remove("is-on"));

/* 复制按钮：复制回答文字，图标变成 ✓，1.5 秒后恢复 */
const copyButton = document.querySelector(".copy-btn");
copyButton.addEventListener("click", () => {
  const text = [...document.querySelectorAll(".answer p")].map((p) => p.textContent).join("\n");
  navigator.clipboard?.writeText(text).catch(() => {});   // 本地 file:// 下可能没有剪贴板权限，忽略即可
  copyButton.classList.add("is-swapped");
  copyButton.querySelector(".btn-label").textContent = "已复制";
  setTimeout(() => {
    copyButton.classList.remove("is-swapped");
    copyButton.querySelector(".btn-label").textContent = "复制";
  }, 1500);
});

/* ======================================== 6. 设计评审面板 ======================================== */
const prefs = { style: "clean", palette: "ink", theme: "auto", font: "sans" };
try {
  Object.assign(prefs, JSON.parse(localStorage.getItem("margin-design") ?? "{}"));
} catch {
  // 隐私模式等情况下 localStorage 不可用，用默认值即可
}
// 地址栏参数优先，方便直接打开某个组合，例如 preview.html?style=sketch&palette=navy&theme=dark
for (const [key, value] of new URLSearchParams(location.search)) {
  if (key in prefs) prefs[key] = value;
}

function applyPrefs() {
  root.dataset.style = prefs.style;
  root.dataset.palette = prefs.palette;
  root.dataset.font = prefs.font;
  if (prefs.theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = prefs.theme;
  for (const b of document.querySelectorAll(".review [data-set]")) {
    b.setAttribute("aria-pressed", String(prefs[b.dataset.set] === b.dataset.value));
  }
  // 衬线只对简洁风生效；瑞士风有固定配色
  document.querySelector('.seg[data-group="font"]').classList.toggle("is-disabled", prefs.style !== "clean");
  document.querySelector('.seg[data-group="palette"]').classList.toggle("is-disabled", prefs.style === "swiss");
  // 换风格后旁注布局方式变了：清掉旧位置，重新对齐
  for (const item of marginItems) delete item.dataset.placed;
  hideConnector();
  scheduleLayout();
}

for (const b of document.querySelectorAll(".review [data-set]")) {
  b.addEventListener("click", () => {
    prefs[b.dataset.set] = b.dataset.value;
    // 颜色渐变 0.3 秒，而不是瞬间跳变
    root.classList.add("theming");
    setTimeout(() => root.classList.remove("theming"), 350);
    applyPrefs();
    try {
      localStorage.setItem("margin-design", JSON.stringify(prefs));
    } catch {
      // 同上
    }
  });
}
document.querySelector(".review .replay").addEventListener("click", play);

const reviewPanel = document.querySelector(".review");
const minimizeButton = reviewPanel.querySelector(".minimize");
function setReviewMin(min) {
  reviewPanel.classList.toggle("is-min", min);
  minimizeButton.textContent = min ? "展开" : "收起";
}
minimizeButton.addEventListener("click", () => setReviewMin(!reviewPanel.classList.contains("is-min")));
// 手机宽度下面板会挡住半屏，默认收起
setReviewMin(matchMedia("(max-width: 760px)").matches);

applyPrefs();
document.fonts.ready.then(scheduleLayout);   // 字体加载完行高会变，重新对齐一次
// 系统开启“减少动态效果”时直接显示最终状态，不自动回放
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) play();
