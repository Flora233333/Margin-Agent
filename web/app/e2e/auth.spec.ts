/*
 * 登录和隔离（M3a）：没登录跳登录页、登录后回原页面；别人的题看不到；密码错和被锁住的提示；
 * 执行中登录过期；退出。
 * 这里的测试自己决定登不登录，所以用 Playwright 原本的 test（support.ts 的 test 会先以 dev 登录）。
 */

import { expect, type Page, test } from '@playwright/test'
import { fixture, loginAs, MOCK_API, PASSWORD, replayId, submit, waitSettled } from './support'

async function fillLogin(page: Page, username: string, password: string) {
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill(password)
  await page.getByRole('button', { name: '登录' }).click()
}

test('没登录打开一道题：跳到登录页（不提示“过期”），登录后回到这道题', async ({ page }) => {
  const id = await replayId(page, 'num_short')

  await page.goto(`/runs/${id}`)

  await expect(page).toHaveURL(`/login?next=%2Fruns%2F${id}`)
  await expect(page.getByText('登录已过期')).toHaveCount(0)
  await expect(page.locator('.sidebar')).toHaveCount(0) // 登录页没有侧栏，看不到任何历史
  await fillLogin(page, 'dev', PASSWORD)
  await expect(page).toHaveURL(`/runs/${id}`)
  await expect(page.locator('.bubble')).toHaveText(fixture('num_short').detail.question)
})

test('A 的题 B 看不到：B 的历史列表里没有，直接打开地址显示“不属于当前账号”', async ({ page, browser }) => {
  await loginAs(page, 'dev')
  const liveId = await submit(page, 'tf') // A（dev）刚提交、正在执行的题
  const replayed = await replayId(page, 'num_short') // A 以前的题
  const other = await browser.newPage() // 另一个浏览器窗口（cookie 不共用）= 另一个人
  await loginAs(other, 'test_b')

  await other.goto('/')
  await expect(other.locator('.account-name')).toContainText('test_b')
  await expect(other.locator('.history .run')).toHaveCount(0)
  for (const id of [liveId, replayed]) {
    await other.goto(`/runs/${id}`)
    await expect(other.getByText('这道题不存在，或者不属于当前账号。')).toBeVisible()
  }
  await expect(page.locator('.history .run').first()).toBeVisible() // A 自己照常能看到
  await other.close()
})

test('密码不对：提示并清空密码；连续试太多被锁住、按钮不能点，时间到了自动恢复、能登录', async ({ page }) => {
  // 假 API 里 test_lock 只能试 3 次、锁 2 秒（真 API 是 10 次、10 分钟）
  await page.goto('/login')
  for (let i = 0; i < 3; i++) {
    await fillLogin(page, 'test_lock', 'wrong password')
    await expect(page.getByRole('alert')).toHaveText('用户名或密码不对。')
    await expect(page.getByLabel('密码')).toHaveValue('')
    await expect(page.getByLabel('密码')).toBeFocused()
  }

  await fillLogin(page, 'test_lock', 'wrong password')
  await expect(page.getByRole('alert')).toHaveText('尝试次数太多，请 1 分钟后再试。')
  await page.getByLabel('密码').fill(PASSWORD)
  await expect(page.getByRole('button', { name: '登录' })).toBeDisabled()

  await expect(page.getByRole('button', { name: '登录' })).toBeEnabled({ timeout: 5000 })
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page).toHaveURL('/')
  await expect(page.locator('.account-name')).toContainText('test_lock')
})

test('执行中登录过期：事件流重连拿到 401 → 跳登录页并提示“登录已过期”；重新登录后回到这道题，执行接着显示完', async ({ page }) => {
  await loginAs(page, 'test_c')
  const id = await submit(page, 'text_long')
  await expect(page.locator('.steps > .step').nth(1)).toBeVisible()

  await page.request.post(`${MOCK_API}/__expire?username=test_c`) // 登录过期，并断开正开着的事件流

  await expect(page).toHaveURL(`/login?next=%2Fruns%2F${id}&expired=1`, { timeout: 15_000 })
  await expect(page.getByText('登录已过期，请重新登录。登录后会回到刚才打开的页面。')).toBeVisible()
  await fillLogin(page, 'test_c', PASSWORD)
  await expect(page).toHaveURL(`/runs/${id}`)
  await waitSettled(page)
})

test('退出：回到登录页；再打开题目页要重新登录，登录前看不到内容', async ({ page }) => {
  await loginAs(page, 'dev')
  const id = await replayId(page, 'num_short')
  await page.goto(`/runs/${id}`)
  await expect(page.locator('.bubble')).toBeVisible()

  await page.locator('.sidebar').hover() // 简洁风的侧栏平时收成图标栏，移上去才展开
  await page.getByRole('button', { name: '退出' }).click()

  await expect(page).toHaveURL('/login')
  await page.goto(`/runs/${id}`)
  await expect(page).toHaveURL(`/login?next=%2Fruns%2F${id}`)
  await expect(page.locator('.bubble')).toHaveCount(0)
})
