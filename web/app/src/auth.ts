/*
 * 登录相关的小工具（纯函数，单元测试见 auth.test.ts）：登录页地址怎么拼、登录后回哪里、被锁住时怎么说。
 */

export const LOGIN_PATH = '/login'

/**
 * 跳到登录页的地址：带上现在的页面（next），登录后回来；expired 表示“之前登录着、现在过期了”，登录页顶上说一声。
 * 本来就在登录页时不再带 next（否则会变成“登录后回到登录页”）。
 */
export function loginPath(current: string, expired: boolean): string {
  const params = new URLSearchParams()
  if (current !== '/' && !current.startsWith(LOGIN_PATH)) {
    params.set('next', current)
  }
  if (expired) {
    params.set('expired', '1')
  }
  const query = params.toString()
  return query ? `${LOGIN_PATH}?${query}` : LOGIN_PATH
}

/**
 * 登录后回到哪里。next 来自地址栏，谁都能改：只接受本站的路径（以一个 / 开头），
 * 不接受 //evil.example 或 https://evil.example——否则别人发一个“登录链接”就能在你登录后把你带到他的网站（开放重定向）。
 */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith(LOGIN_PATH)) {
    return '/'
  }
  return next
}

/** 被锁住时的提示：剩余秒数向上取整成分钟，和后端 429 的说法一致 */
export function lockedMessage(secondsLeft: number): string {
  return `尝试次数太多，请 ${Math.ceil(secondsLeft / 60)} 分钟后再试。`
}
