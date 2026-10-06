/*
 * 看着一道题执行完：回答写完后过程区停一下再收起 → 结论淡入、数字滚动 → 正文逐字 → 账目行（Answer.tsx）。
 * 不同类型、长短的题各一道（录自真实运行，见 e2e/fixtures/）。
 * 防的事故：大字比正文晚一拍“插进来”、过程区收起后满屏空白、撰写失败时什么都看不到。
 */

import { expect, test } from '@playwright/test'
import { collectErrors, firstAt, frames, itemCount, recordFrames, submit, waitSettled } from './support'

interface Case {
  name: string
  label: string
  figure: string | null // 结论行的大字；null = 开放问题，不显示结论行
  counts?: boolean // 结论是单个数，应该从 0 滚上去
  failedCompose?: boolean
}

const CASES: Case[] = [
  { name: 'num_short', label: '数值，5 步', figure: '45027.00', counts: true },
  { name: 'num_unit', label: '数值，模型交的带单位“1,332 亿元”', figure: '1332.00', counts: true },
  { name: 'num_multi', label: '多值计算题，8 步', figure: '40.05、10.10' },
  { name: 'pct', label: '百分比', figure: '62.33%', counts: true },
  { name: 'date', label: '日期', figure: '2026年1月1日' },
  { name: 'tf', label: '判断，4 步', figure: '正确' },
  { name: 'text_long', label: '开放问题，13 步', figure: null },
  { name: 'escalated', label: '语料里没有，放弃作答', figure: '未作答' },
  { name: 'compose_failed', label: '撰写回答失败', figure: '0.95%', counts: true, failedCompose: true },
]

for (const c of CASES) {
  test(`看着执行结束（${c.label}）：过程区收起 → 结论 → 正文逐字 → 账目行`, async ({ page }) => {
    const errors = collectErrors(page)
    await recordFrames(page)
    await submit(page, c.name)
    await waitSettled(page)
    const list = await frames(page)

    // 回答写完之前，回答区什么都不出（撰写进度显示在过程区里）
    const fold = firstAt(list, (f) => !f.open)
    expect(list.filter((f) => f.t < fold).some((f) => f.verdict || f.textLen > 0)).toBe(false)

    const verdictAt = firstAt(list, (f) => f.verdict)
    const textAt = firstAt(list, (f) => f.textLen > 0)
    const footAt = firstAt(list, (f) => f.foot)
    if (c.figure === null) {
      expect(verdictAt).toBe(Infinity)
      expect(fold).toBeLessThan(textAt)
    } else {
      expect(fold).toBeLessThanOrEqual(verdictAt)
      // 撰写失败时没有正文，账目行和结论几乎同时出现，数字可能还在滚：等它停在最终值
      await expect(page.locator('.attempt:last-child .verdict-figure')).toHaveText(c.figure)
      if (!c.failedCompose) {
        expect(verdictAt).toBeLessThan(textAt) // 大字先出，正文后出
      }
    }
    if (c.counts) {
      const seen = list.map((f) => f.figure).filter(Boolean)
      expect(seen[0]).not.toBe(c.figure) // 从 0 滚上去，不是直接出现最终的数
    }
    if (c.failedCompose) {
      await expect(page.locator('.attempt:last-child .answer-note')).toContainText('回答生成失败')
      await expect(page.locator('.attempt:last-child .evidence li').first()).toBeVisible()
    } else {
      expect(textAt).toBeLessThan(footAt)
      // 正文是一段段放出来的，不是一下子全出现
      const lengths = new Set(list.filter((f) => f.t < footAt).map((f) => f.textLen))
      expect(lengths.size).toBeGreaterThanOrEqual(4)
    }

    // 过程区里每一步都在，没有重复（理解题目 + 思考 + 工具 + 撰写回答）
    await expect(page.locator('.attempt:last-child .steps > .step')).toHaveCount(itemCount(c.name))
    // 收起之后回答在屏幕里（M2.5 修订四之前，长题会被滚到屏幕上方几千像素外）
    const box = (await page.locator('.attempt:last-child .answer').boundingBox())!
    expect(box.y).toBeLessThan(page.viewportSize()!.height)
    expect(box.y + box.height).toBeGreaterThan(0)
    expect(errors).toEqual([])
  })
}
