/*
 * 登录页 /login（设计稿 web/design/login.html 的方案 A“居中卡片”，取舍 28）。
 * 没有注册入口：账号由管理员用 scripts/create_user.py 建（D30）。
 *
 * 地址参数（App 跳过来时带上，见 auth.ts 的 loginPath）：
 *   next     登录后回到哪个页面（只接受本站路径，safeNext）
 *   expired  1 = 之前登录着、现在过期了，卡片顶上说一声
 *
 * 几种状态（和设计稿一一对应）：
 *   登录中        按钮转圈“登录中…”、不能再点，输入框只读（防止连点、或提交后又改了内容）
 *   密码不对      401：密码框标红并清空、焦点回到密码框；不说是用户名错还是密码错（后端也不说）
 *   尝试太多      429：按 Retry-After 倒计时，按钮不能点，时间到了自动恢复
 *   其他错误      连不上服务器、503 等：同一个位置显示后端给的说明
 */

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { ApiError, login } from '../api'
import { lockedMessage } from '../auth'
import { Icon } from '../components/Icons'

export function LoginPage({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [expired] = useState(() => new URLSearchParams(window.location.search).get('expired') === '1')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [wrong, setWrong] = useState(false)
  const [error, setError] = useState('')
  const [lockedUntil, setLockedUntil] = useState(0) // 被锁到什么时刻（Date.now() 的毫秒），0 = 没锁
  const [now, setNow] = useState(() => Date.now())
  const passwordRef = useRef<HTMLInputElement>(null)

  const secondsLeft = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const locked = secondsLeft > 0

  // 被锁住时每秒走一次表：提示里的分钟数跟着变，到点后按钮自动恢复
  useEffect(() => {
    if (!locked) {
      return
    }
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [locked])

  const canSubmit = username.trim() !== '' && password !== '' && !submitting && !locked

  async function submit(e: FormEvent) {
    e.preventDefault() // 不让浏览器按表单的默认方式整页提交
    if (!canSubmit) {
      return
    }
    setSubmitting(true)
    setWrong(false)
    setError('')
    try {
      const me = await login(username.trim(), password)
      onLoggedIn(me.username)
    } catch (e) {
      const err = e as ApiError
      if (err.status === 401) {
        setWrong(true)
        setPassword('')
        passwordRef.current?.focus()
      } else if (err.status === 429) {
        setLockedUntil(Date.now() + err.retryAfter * 1000)
        setNow(Date.now())
      } else {
        setError(err.message)
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit} noValidate>
        <div className="login-head">
          <div className="login-brand">
            <span className="brand-mark">M</span>argin
          </div>
          <h1 className="login-title">登录</h1>
          <p className="login-sub">登录后查看你提交过的问题和回答。</p>
        </div>
        {expired && (
          <p className="login-notice">
            <Icon name="clock" />
            <span>登录已过期，请重新登录。登录后会回到刚才打开的页面。</span>
          </p>
        )}
        <label className="login-field">
          <span className="login-label">用户名</span>
          <input
            className="login-input"
            name="username"
            autoComplete="username"
            autoFocus
            readOnly={submitting}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="login-field">
          <span className="login-label">密码</span>
          <input
            ref={passwordRef}
            className="login-input"
            name="password"
            type="password"
            autoComplete="current-password"
            readOnly={submitting}
            aria-invalid={wrong}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {(wrong || locked || error) && (
          <p className="login-error" role="alert">
            <Icon name="alert" />
            <span>{locked ? lockedMessage(secondsLeft) : wrong ? '用户名或密码不对。' : error}</span>
          </p>
        )}
        <button className="login-submit" type="submit" disabled={!canSubmit} aria-busy={submitting}>
          {submitting && <span className="login-spinner" aria-hidden="true" />}
          {submitting ? '登录中…' : '登录'}
        </button>
      </form>
      <p className="login-foot">账号由管理员创建</p>
    </main>
  )
}
