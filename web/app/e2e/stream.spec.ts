/*
 * 执行过程中的交互：思考逐字出现、刷新页面、连接“半死”、重新生成。
 * 每个测试防一个出过的事故（括号里是当时的修订，见 review/02_代码导读/）。
 */

import { expect, type Page, test } from '@playwright/test'
import { collectErrors, frames, itemCount, MOCK_API, recordFrames, submit, waitSettled } from './support'

/** 等过程区里至少有 n 项（一轮会同时多出思考和工具两项，“恰好 n 项”可能被跳过） */
async function atLeast(page: Page, n: number): Promise<void> {
  await page.waitForFunction((count) => document.querySelectorAll('.steps > .step').length >= count, n, { timeout: 15_000 })
}

test('思考流式输出完、这一步的结果到达时，同一个条目原地换文字，不整段重播（M2.5-5 修订一）', async ({ page }) => {
  await submit(page, 'text_long')
  const live = page.locator('.steps > .step.is-current').filter({ has: page.locator('.thought-text') }).first()
  await expect(live).toBeVisible()
  const item = await live.elementHandle()
  // 等这一轮的 step 到达：条目不再是“正在进行”。记下到达的时刻
  const arrived = await page.waitForFunction((el) => !el!.classList.contains('is-current') && performance.now(),
    item, { timeout: 15_000 })
  const kept = await item!.evaluate((el, at) => ({
    connected: el.isConnected, // 还是原来那个元素，没有被删掉重建
    // 到达之后没有重新开始的入场动画（最初插入时那一次可能还没播完，不算）
    replayed: el.getAnimations().some((a) => (a as CSSAnimation).animationName === 'step-in'
      && (a.startTime as number) >= at),
  }), await arrived.jsonValue() as number)
  expect(kept).toEqual({ connected: true, replayed: false })
})

test('理解题目从“正在理解”换成标题，这一项高度不变，下面的条目不被顶一下（M2.5-5 修订四）', async ({ page }) => {
  await recordFrames(page)
  await submit(page, 'num_multi')
  await waitSettled(page)
  const list = await frames(page)
  // 理解结果到达前的最后一刻（这时出现动画早已播完，高度长到位了）和之后的每一刻比
  const pending = list.filter((f) => f.understand === '正在理解题目…').at(-1)!
  const done = list.filter((f) => f.t > pending.t)
  expect(pending.understandH).toBeGreaterThan(0)
  expect(done.every((f) => f.understandH === pending.understandH)).toBe(true)
})

test('窗口很矮、过程区很长：回答写完收起后，结论在屏幕里，不是满屏空白（M2.5-5 修订四）', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 600 })
  await submit(page, 'num_multi')
  await waitSettled(page)
  await page.waitForTimeout(1500)
  const verdict = (await page.locator('.attempt:last-child .verdict').boundingBox())!
  expect(verdict.y).toBeGreaterThanOrEqual(0)
  expect(verdict.y).toBeLessThan(600)
  // 页面能滚动的高度跟着内容变矮了（连线的 SVG 不再撑住页面）
  const extra = await page.evaluate(() => {
    const app = document.querySelector('.app')!
    const bottom = document.querySelector('.composer')!.getBoundingClientRect().bottom + app.scrollTop
    return app.scrollHeight - bottom
  })
  expect(extra).toBeLessThan(100)
})

test('执行中刷新页面：补发的历史卡片不画连线，之后的步骤照常到达（M2.5-5 修订二）', async ({ page }) => {
  const errors = collectErrors(page)
  await submit(page, 'text_long')
  await atLeast(page, 5)
  await page.reload()
  // 刷新后 2 秒内（补发历史的时候）连线一次都没出现
  const drawn = await page.evaluate(() => new Promise<boolean>((resolve) => {
    const started = performance.now()
    const timer = setInterval(() => {
      if (document.querySelector('.connector path.is-on')) {
        clearInterval(timer)
        resolve(true)
      } else if (performance.now() - started > 2000) {
        clearInterval(timer)
        resolve(false)
      }
    }, 20)
  }))
  expect(drawn).toBe(false)
  await waitSettled(page)
  await expect(page.locator('.steps > .step')).toHaveCount(itemCount('text_long'))
  expect(errors).toEqual([])
})

test('连接“半死”（不发数据也不断开）：25 秒后自动重连续上，步骤不重复、不缺（M2-8）', async ({ page }) => {
  const streams: string[] = []
  page.on('request', (r) => r.url().includes('/events') && streams.push(new URL(r.url()).search))
  const id = await submit(page, 'text_long')
  await atLeast(page, 4)
  await page.request.post(`${MOCK_API}/__stall/${id}`)
  const stuckAt = await page.locator('.steps > .step').count()
  await page.waitForTimeout(5000)
  // 连接卡住了：这段时间页面上什么都没多（假 API 那边其实还在往下执行）
  await expect(page.locator('.steps > .step')).toHaveCount(stuckAt)

  await waitSettled(page)
  // 25 秒没收到任何数据，前端关掉旧连接、从最后收到的 seq 之后重新订阅
  expect(streams.length).toBeGreaterThanOrEqual(2)
  expect(Number(new URLSearchParams(streams.at(-1)).get('after'))).toBeGreaterThan(0)
  await expect(page.locator('.steps > .step')).toHaveCount(itemCount('text_long'))
})

test('执行中点“重新生成”被拒绝并提示；结束后再点，第 2 次执行按顺序出场（M2-7）', async ({ page }) => {
  await submit(page, 'regenerated')
  await atLeast(page, 3)
  await page.getByRole('button', { name: '重新生成' }).click()
  await expect(page.locator('.form-error')).toContainText('这道题还在执行中')

  await waitSettled(page)
  await page.getByRole('button', { name: '重新生成' }).click()
  await expect(page.locator('.attempt-head')).toHaveText(['第 1 次执行', '第 2 次执行（重新生成）'])
  await waitSettled(page)
  await expect(page.locator('.attempt:last-child .steps > .step')).toHaveCount(itemCount('regenerated'))
  // 第 1 次执行只留结论；来源和账目行属于最近一次
  await expect(page.locator('.answer.is-earlier .answer-foot')).toHaveCount(0)
})

test('执行失败：显示原因，不显示回答区', async ({ page }) => {
  await submit(page, 'failed')
  await expect(page.locator('.failure')).toContainText('模型网关返回错误（HTTP 500）', { timeout: 15_000 })
  await expect(page.locator('.answer')).toHaveCount(0)
})
