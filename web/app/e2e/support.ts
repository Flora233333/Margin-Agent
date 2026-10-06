/*
 * 端到端测试的公共部分：读固定数据、像用户一样提交问题、记录页面随时间的变化。
 */

import { readFileSync } from 'node:fs'
import { expect, type Page } from '@playwright/test'

export const MOCK_API = 'http://127.0.0.1:8100'

interface Fixture {
  detail: { question: string }
  events: { seq: number; type: string; data: Record<string, any> }[]
}

export function fixture(name: string): Fixture {
  return JSON.parse(readFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), 'utf8'))
}

/** 最后一次执行在过程区里应有几项：理解题目（第一次执行）+ 每一步的思考和工具调用 + 撰写回答 */
export function itemCount(name: string): number {
  const events = fixture(name).events
  const queued = events.findLastIndex((e) => e.type === 'attempt_queued')
  const last = events.slice(queued) // 最后一次执行的事件
  const steps = last.filter((e) => e.type === 'step')
  const understood = last.some((e) => e.type === 'run_understood')
  const written = last.some((e) => e.type === 'answer_written')
  return (understood ? 1 : 0) + steps.length + steps.filter((s) => s.data.reasoning).length + (written ? 1 : 0)
}

/** 已结束的题在假 API 里的 id（打开就是全部历史） */
export async function replayId(page: Page, name: string): Promise<number> {
  const ids = await (await page.request.get(`${MOCK_API}/__ids`)).json()
  return ids[name]
}

/** 和用户一样：打开首页，输入问题，点发送，等跳到这道题的页面。返回 run id */
export async function submit(page: Page, name: string): Promise<number> {
  await page.goto('/')
  await page.locator('textarea').fill(fixture(name).detail.question)
  await page.locator('.send').click()
  await page.waitForURL(/\/runs\/\d+$/)
  return Number(page.url().split('/').pop())
}

export interface Frame {
  t: number
  open: boolean // 最后一次执行的过程区是否展开
  verdict: boolean
  figure: string | null
  textLen: number // 回答正文已经显示的字数
  foot: boolean // 账目行
  understand: string // 过程区第一项的文字
  understandH: number
}

/**
 * 在页面里每 20 毫秒记一次状态（有变化才记），测试结束后取出来看先后顺序。
 * 用 addInitScript：页面一加载就开始记，包括刷新之后。
 */
export async function recordFrames(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const frames: unknown[] = []
    ;(window as any).__frames = frames
    let last = ''
    const start = performance.now()
    setInterval(() => {
      const attempt = document.querySelector('.attempt:last-child')
      if (!attempt) {
        return
      }
      const first = attempt.querySelector('.steps > .step')
      const frame = {
        open: !!attempt.querySelector('.work.is-open'),
        verdict: !!attempt.querySelector('.answer .verdict'),
        figure: attempt.querySelector('.verdict-figure')?.textContent ?? null,
        textLen: [...attempt.querySelectorAll('.answer > p:not(.answer-note)')].map((p) => p.textContent).join('').length,
        foot: !!attempt.querySelector('.answer-foot'),
        understand: first?.textContent ?? '',
        understandH: Math.round(first?.getBoundingClientRect().height ?? 0),
      }
      const key = JSON.stringify(frame)
      if (key !== last) {
        last = key
        frames.push({ t: Math.round(performance.now() - start), ...frame })
      }
    }, 20)
  })
}

export async function frames(page: Page): Promise<Frame[]> {
  return page.evaluate(() => (window as any).__frames)
}

/** 第一个满足条件的时刻；没有就是 Infinity */
export function firstAt(list: Frame[], when: (f: Frame) => boolean): number {
  return list.find(when)?.t ?? Infinity
}

/** 页面没有报错：未捕获的异常、控制台错误都算 */
export function collectErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  return errors
}

export async function waitSettled(page: Page): Promise<void> {
  await expect(page.locator('.attempt:last-child .answer-foot, .attempt:last-child .failure')).toBeVisible({ timeout: 60_000 })
}
