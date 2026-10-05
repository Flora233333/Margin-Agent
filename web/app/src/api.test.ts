import { describe, expect, test } from 'vitest'
import { describeError } from './api'

describe('describeError', () => {
  test('422 字段错误变成一句中文，而不是把 JSON 原样显示给用户', () => {
    // FastAPI 对超长问题 + 未知字段返回的 detail（结构与真实响应相同）
    const detail = [
      { loc: ['body', 'question'], msg: 'String should have at most 2000 characters', type: 'string_too_long' },
      { loc: ['body', 'foo'], msg: 'Extra inputs are not permitted', type: 'extra_forbidden' },
    ]
    expect(describeError(422, detail)).toBe('问题最多 2000 字；foo是多余的字段')
  })

  test('409 等业务错误直接显示后端给的说明', () => {
    expect(describeError(409, '这道题还在执行中')).toBe('这道题还在执行中')
  })

  test('转发失败时响应不是 JSON，也要给出说明而不是 undefined', () => {
    expect(describeError(502, undefined)).toBe('请求失败（HTTP 502）')
  })
})
