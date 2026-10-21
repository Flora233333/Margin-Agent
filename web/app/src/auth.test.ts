import { expect, test } from 'vitest'
import { lockedMessage, loginPath, safeNext } from './auth'

test('登录过期跳登录页时带上原来的页面，登录后能回去', () => {
  expect(loginPath('/runs/12', true)).toBe('/login?next=%2Fruns%2F12&expired=1')
  expect(loginPath('/runs/12', false)).toBe('/login?next=%2Fruns%2F12')
  expect(loginPath('/', false)).toBe('/login')
})

test('已经在登录页时不再把登录页当成“原来的页面”', () => {
  expect(loginPath('/login', true)).toBe('/login?expired=1')
})

test('登录后只回本站的页面，别人塞进地址栏的外站地址一律回首页', () => {
  expect(safeNext('/runs/12')).toBe('/runs/12')
  expect(safeNext(null)).toBe('/')
  expect(safeNext('https://evil.example')).toBe('/')
  expect(safeNext('//evil.example/runs/1')).toBe('/')
  expect(safeNext('/login?next=/runs/1')).toBe('/')
})

test('被锁住的提示按分钟向上取整：还剩 9 分 1 秒说 10 分钟', () => {
  expect(lockedMessage(541)).toBe('尝试次数太多，请 10 分钟后再试。')
  expect(lockedMessage(60)).toBe('尝试次数太多，请 1 分钟后再试。')
  expect(lockedMessage(1)).toBe('尝试次数太多，请 1 分钟后再试。')
})
