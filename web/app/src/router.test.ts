import { expect, test } from 'vitest'
import { matchRoute } from './router'

test('题目页地址解析出 run id', () => {
  expect(matchRoute('/runs/12')).toEqual({ page: 'run', runId: 12 })
})

test('不认识的地址显示 404，而不是空白页或把 abc 当成 run id', () => {
  expect(matchRoute('/runs/abc')).toEqual({ page: 'not-found' })
  expect(matchRoute('/settings')).toEqual({ page: 'not-found' })
})
