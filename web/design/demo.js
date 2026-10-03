/*
 * 设计稿的回放脚本：只用来演示动效，不是产品代码。
 *
 * 页面里每个带 data-seq="N" 的元素都是一个 .reveal 展开容器，编号相同的同时出现。
 * 回放流程：
 *   1. 先把所有 data-seq 元素瞬间收起（临时关闭过渡），清空要“打字”的文字；
 *   2. 按编号依次展开：高度从 0 平滑长出 + 淡入，像从上一条下面滑出来；
 *   3. 带 data-stream 的段落逐字淡入（模拟模型流式输出）；
 *   4. 过程全部结束后，过程区自动折叠成一行摘要，再输出回答。
 * 其他可选属性：data-wait 出现前等待的毫秒数；data-label 运行中过程区标题；data-finish 先结束过程区。
 */

const root = document.documentElement;
const main = document.querySelector(".main");
const work = document.querySelector(".work");
const workHead = work.querySelector(".work-head");
const workLabel = work.querySelector(".work-label");

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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let runId = 0;   // 每次回放加 1；旧的一轮发现编号变了就停下

/* 逐字输出：文字每次 2 个字，元素（如引用编号）整体出现，保证 [1] 不会被拆开 */
async function stream(el, id) {
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
      await sleep(80);
      if (id !== runId) return;
    }
  }
}

function streamsIn(el) {
  return el.matches("[data-stream]") ? [el] : [...el.querySelectorAll("[data-stream]")];
}

function followBottom() {
  main.scrollTo({ top: main.scrollHeight, behavior: "smooth" });
}

function markCurrent(step) {
  for (const s of document.querySelectorAll(".step.is-current")) s.classList.remove("is-current");
  step?.classList.add("is-current");
}

function setOpen(open) {
  work.classList.toggle("is-open", open);
  workHead.setAttribute("aria-expanded", String(open));
}

function resetAll() {
  root.classList.add("no-transition");
  for (const el of items) el.classList.add("is-hidden");
  for (const el of streams) el.replaceChildren();
  work.classList.add("is-running");
  workLabel.textContent = "正在思考";
  setOpen(true);
  markCurrent(null);
  main.scrollTo({ top: 0 });
  // 读一次布局，强制浏览器先应用“收起”状态；否则下面去掉 is-hidden 时不会有过渡
  void root.offsetHeight;
  root.classList.remove("no-transition");
}

async function finishWork(id) {
  markCurrent(null);
  work.classList.remove("is-running");
  workLabel.textContent = work.dataset.doneLabel;
  await sleep(700);
  if (id !== runId) return;
  setOpen(false);
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
    if (first.dataset.label) workLabel.textContent = first.dataset.label;

    for (const el of group) {
      el.classList.remove("is-hidden");
      if (el.classList.contains("step")) markCurrent(el);
    }
    followBottom();

    for (const el of group) {
      for (const s of streamsIn(el)) {
        await stream(s, id);
        if (id !== runId) return;
        followBottom();
      }
    }
  }
}

/* 点击过程区标题：展开 / 折叠 */
workHead.addEventListener("click", () => setOpen(!work.classList.contains("is-open")));

/* 鼠标移到引用编号或来源卡片上，两边同时高亮 */
function linkSources(event, on) {
  const el = event.target.closest("[data-src]");
  if (!el) return;
  for (const x of document.querySelectorAll(`[data-src="${el.dataset.src}"]`)) {
    x.classList.toggle("is-linked", on);
  }
}
document.addEventListener("mouseover", (event) => linkSources(event, true));
document.addEventListener("mouseout", (event) => linkSources(event, false));

/* ---------------- 设计评审面板：风格 / 配色 / 深浅，偏好存在浏览器本地 ---------------- */
const prefs = { style: "clean", palette: "ink", theme: "auto" };
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
  if (prefs.theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = prefs.theme;
  for (const b of document.querySelectorAll(".review [data-set]")) {
    b.setAttribute("aria-pressed", String(prefs[b.dataset.set] === b.dataset.value));
  }
}

for (const b of document.querySelectorAll(".review [data-set]")) {
  b.addEventListener("click", () => {
    prefs[b.dataset.set] = b.dataset.value;
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
// 系统开启“减少动态效果”时直接显示最终状态，不自动回放
if (!matchMedia("(prefers-reduced-motion: reduce)").matches) play();
