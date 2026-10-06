/*
 * 打开一道已经结束的题（从历史进入、分享链接）：全部直接显示，不播“看着它结束”的出场顺序。
 * 防的事故：补发的历史事件也经过“执行中”，被当成实时的，每次打开都重播收起、逐字打字。
 */

import { expect, test } from '@playwright/test'
import { collectErrors, firstAt, frames, itemCount, recordFrames, replayId } from './support'

for (const name of ['num_short', 'pct', 'text_long', 'escalated', 'compose_failed']) {
  test(`打开已结束的题（${name}）：过程区收着，结论、正文、账目行直接显示`, async ({ page }) => {
    const errors = collectErrors(page)
    await recordFrames(page)
    await page.goto(`/runs/${await replayId(page, name)}`)
    await expect(page.locator('.answer-foot')).toBeVisible()
    await page.waitForTimeout(1500) // 数字滚完
    const list = await frames(page)

    expect(list.some((f) => f.open)).toBe(false)
    const textAt = firstAt(list, (f) => f.textLen > 0)
    if (textAt !== Infinity) {
      // 正文第一次出现就是全文，不是一个字一个字打出来的
      expect(list.find((f) => f.t === textAt)!.textLen).toBe(list.at(-1)!.textLen)
    }
    await expect(page.locator('.steps > .step')).toHaveCount(itemCount(name))
    expect(errors).toEqual([])
  })
}

test('打开重新生成过的题：两次执行都在，更早的一次只留结论', async ({ page }) => {
  const errors = collectErrors(page)
  await page.goto(`/runs/${await replayId(page, 'regenerated')}`)
  await expect(page.locator('.attempt-head')).toHaveText(['第 1 次执行', '第 2 次执行（重新生成）'])
  await expect(page.locator('.attempt:last-child .answer-foot')).toBeVisible()
  // 引用编号和右侧来源只属于最近一次执行；更早的一次没有账目行
  await expect(page.locator('.answer.is-earlier .answer-foot')).toHaveCount(0)
  await expect(page.locator('.answer.is-earlier')).toBeVisible()
  expect(errors).toEqual([])
})

test('打开执行失败的题：显示失败原因和“重新生成”的提示', async ({ page }) => {
  await page.goto(`/runs/${await replayId(page, 'failed')}`)
  await expect(page.locator('.failure')).toContainText('模型网关返回错误（HTTP 500）')
  await expect(page.locator('.answer')).toHaveCount(0)
})
