/*
 * 端到端测试用的假 API：接口和真 API（src/margin/api.py）一样，内容来自录好的固定数据（e2e/fixtures/）。
 * 不起数据库、不调模型，几秒钟跑完一道题；浏览器那一侧（页面、SSE、Vite 转发）都是真的。
 *
 * 两种题：
 *   已结束的题  启动时每份固定数据一道（id 1、2、3……），打开就是全部历史，和真 API 一样补发完就关闭连接；
 *   实时的题    在页面上提交固定数据里的那个问题时新建（id 从 1001 起），按录下的顺序、加上间隔一条条推出来。
 *               实时片段（思考、回答正文）不落库、录不到，用每一步的完整思考、撰写好的回答切成片段补上。
 *
 * 推流的规则和真 API 相同：先补发 after 之后已经发生的事件，再发 caught_up，之后实时推；
 * 这次执行结束（attempt_finished / attempt_failed）后关闭连接。重新生成时推下一次执行的录制（有的话）。
 *
 * 登录（M3a）：接口、状态码、cookie 和真 API 一样，账号写死在 USERS 里（密码都是 PASSWORD）。
 *   没登录访问 /auth/me、/runs… 都是 401；别人的题 404；已结束的题都属于 dev，实时的题属于提交它的人。
 *   登录限流：同一个用户名最多试 10 次、锁 600 秒；test_lock 这个账号只能试 3 次、锁 2 秒，测试不用等 10 分钟。
 *
 * 给测试用的控制接口（真 API 没有）：
 *   GET  /__ids          固定数据名 -> 已结束的题的 id
 *   POST /__stall/{id}?down_ms=N
 *                        这道题现有的连接从此一个字都不发、也不关（模拟“半死”的连接）；
 *                        之后 N 毫秒内新连接都返回 502（模拟 API 进程重启中，Vite 代理就是这样回的），再之后照常
 *   POST /__slow?name=X&batches=N&gap_ms=M
 *                        新建一道已结束的题（固定数据 X），打开时补发的历史分成 N 批、每批之间隔 M 毫秒（模拟慢网络），
 *                        返回 { id }。不出现在 /__ids 里，不影响按名字打开的其他测试
 *   POST /__expire?username=X
 *                        让这个用户的登录全部过期，并断开他正开着的事件流（浏览器会重连，重连时拿到 401）
 *
 * 用法：node e2e/mock-api.mjs（端口 MOCK_PORT，默认 8100）。由 playwright.config.ts 自动启动。
 */

import { readdirSync, readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'

const PORT = Number(process.env.MOCK_PORT ?? 8100)
const PING_MS = 10_000 // 和真 API 一样，10 秒没有数据发一个心跳
// 实时的题的节奏（毫秒）：比真实快很多，但每一步、每个片段都分开到达，页面的动效照样会播
const GAP = { queued: 300, event: 250, chunk: 15 }
const CHUNKS = 30 // 一段思考 / 回答最多切成几片

const fixtures = new Map()
for (const file of readdirSync(new URL('./fixtures/', import.meta.url)).sort()) {
  const fixture = JSON.parse(readFileSync(new URL(`./fixtures/${file}`, import.meta.url), 'utf8'))
  fixtures.set(file.replace(/\.json$/, ''), fixture)
}

const runs = new Map()
let nextLiveId = 1001

/** 按 attempt_queued 把录下的事件分成每次执行一段 */
function attemptsOf(events) {
  const segments = []
  for (const event of events) {
    if (event.type === 'attempt_queued') {
      segments.push([])
    }
    segments.at(-1).push(event)
  }
  return segments
}

function newRun(id, name, live, owner = 'dev') {
  const fixture = fixtures.get(name)
  const run = {
    id, name, fixture, live, owner,
    segments: attemptsOf(fixture.events),
    played: 0, // 已经开始推的执行段数
    published: [], // 已经发生的持久事件
    subscribers: new Set(),
    running: false,
    downUntil: 0, // 在这个时刻之前 /events 返回 502
  }
  if (!live) {
    run.published = fixture.events
    run.played = run.segments.length
  }
  runs.set(id, run)
  return run
}

let replayId = 1
for (const name of fixtures.keys()) {
  newRun(replayId++, name, false)
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function pieces(text) {
  const size = Math.max(4, Math.ceil(text.length / CHUNKS))
  const out = []
  for (let i = 0; i < text.length; i += size) {
    out.push(text.slice(i, i + size))
  }
  return out
}

function broadcast(run, message) {
  for (const subscriber of run.subscribers) {
    subscriber(message)
  }
}

/** 推一次执行：持久事件按顺序发生，step 之前先推这一步的思考片段，answer_written 之前先推回答片段 */
async function play(run, segment) {
  run.running = true
  let current = null
  for (const [i, event] of segment.entries()) {
    await sleep(event.type === 'attempt_started' ? GAP.queued : GAP.event)
    if (event.type === 'attempt_started') {
      current = event.data
    }
    const delta = (turn, kind, text) => ({
      type: 'delta', data: { attempt_id: current.attempt_id, epoch: current.epoch, turn, kind, text },
    })
    if (event.type === 'step' && event.data.reasoning) {
      for (const text of pieces(event.data.reasoning)) {
        broadcast(run, delta(event.data.step_no, 'reasoning', text))
        await sleep(GAP.chunk)
      }
    }
    if (event.type === 'answer_written' && event.data.text) {
      const turn = run.published.filter((e) => e.type === 'step' && e.data.attempt_id === current.attempt_id).length
      for (const text of pieces(event.data.text)) {
        broadcast(run, delta(turn, 'answer', text))
        await sleep(GAP.chunk)
      }
    }
    // 最后一个事件发出之前就算结束：订阅者据此在发完它之后关闭连接
    run.running = i < segment.length - 1
    run.published.push(event)
    broadcast(run, event)
  }
}

function finished(event) {
  return event.type === 'attempt_finished' || event.type === 'attempt_failed'
}

/** 题目详情：实时的题按已经发生的事件拼出当前的样子（标题理解完才有，执行没结束就没有结束时间） */
function detailOf(run) {
  const recorded = run.fixture.detail
  if (!run.live) {
    return { ...recorded, id: run.id }
  }
  const understood = run.published.find((e) => e.type === 'run_understood')?.data
  const done = new Set(run.published.filter(finished).map((e) => e.data.attempt_id))
  const queued = run.published.filter((e) => e.type === 'attempt_queued').length
  const attempts = recorded.attempts.slice(0, queued).map((attempt, i) => {
    const id = run.segments[i][0].data.attempt_id
    return done.has(id) ? attempt : { ...attempt, status: 'running', final: null, finished_at: null }
  })
  return {
    ...recorded,
    id: run.id,
    status: run.running ? 'running' : recorded.status,
    title: understood?.title ?? (run.played > 1 ? recorded.title : null),
    answer_label: understood?.label ?? (run.played > 1 ? recorded.answer_label : null),
    guessed_format: understood?.answer_format ?? (run.played > 1 ? recorded.guessed_format : null),
    attempts,
  }
}

function sendJson(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify(body))
}

function readBody(request) {
  return new Promise((resolve) => {
    let body = ''
    request.on('data', (chunk) => (body += chunk))
    request.on('end', () => resolve(body ? JSON.parse(body) : {}))
  })
}

/** 慢网络：已结束的题，补发的历史分几批写出，批与批之间停一会儿，写完关闭 */
async function writeInBatches(response, events, write, { batches, gap }) {
  const size = Math.ceil(events.length / batches)
  for (let i = 0; i < events.length; i += size) {
    if (i > 0) {
      await sleep(gap)
    }
    events.slice(i, i + size).forEach(write)
  }
  response.end()
}

function stream(request, response, run, after) {
  response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
  const write = (event) => {
    if (event.type === 'delta') {
      response.write(`event: delta\ndata: ${JSON.stringify(event.data)}\n\n`)
    } else {
      response.write(`id: ${event.seq}\nevent: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`)
    }
  }
  const backlog = run.published.filter((e) => e.seq > after)
  if (run.slow) {
    void writeInBatches(response, backlog, write, run.slow)
    return
  }
  for (const event of backlog) {
    write(event)
  }
  // 和真 API 一样：执行已经结束就补发完关闭（不发 caught_up），前端据此判断“已结束、不用重连”
  if (!run.running) {
    response.end()
    return
  }
  response.write('event: caught_up\ndata: {}\n\n')
  const subscriber = (event) => {
    if (subscriber.stalled || response.writableEnded) {
      return
    }
    write(event)
    if (event.type !== 'delta' && !run.running) {
      response.end()
    }
  }
  const ping = setInterval(() => subscriber.stalled || response.write('event: ping\ndata: {}\n\n'), PING_MS)
  subscriber.user = run.owner
  subscriber.end = () => response.end()
  run.subscribers.add(subscriber)
  request.on('close', () => {
    clearInterval(ping)
    run.subscribers.delete(subscriber)
  })
}

// ---------------------------------------------------------------- 登录（M3a）

const PASSWORD = 'e2e password'
const USERS = new Set(['dev', 'test_b', 'test_c', 'test_lock'])
const COOKIE = 'margin_session'
const sessions = new Map() // 令牌 -> 用户名
const attempts = new Map() // 用户名 -> 最近的尝试时刻（毫秒）

function limitOf(username) {
  return username === 'test_lock' ? { max: 3, ms: 2000 } : { max: 10, ms: 600_000 }
}

function tokenOf(request) {
  return /(?:^|;\s*)margin_session=([^;]+)/.exec(request.headers.cookie ?? '')?.[1]
}

function currentUser(request) {
  return sessions.get(tokenOf(request))
}

/** 和真 API 一样：先计数（滑动窗口）再验密码，超了 429 + Retry-After；成功清零、下发 HttpOnly cookie */
function login(response, { username, password }) {
  const limit = limitOf(username)
  const now = Date.now()
  const recent = (attempts.get(username) ?? []).filter((t) => t > now - limit.ms)
  if (recent.length >= limit.max) {
    response.setHeader('Retry-After', String(Math.ceil((recent[0] + limit.ms - now) / 1000)))
    return sendJson(response, 429, { detail: '尝试次数太多' })
  }
  attempts.set(username, [...recent, now])
  if (!USERS.has(username) || password !== PASSWORD) {
    return sendJson(response, 401, { detail: '用户名或密码不对' })
  }
  attempts.delete(username)
  const token = randomUUID()
  sessions.set(token, username)
  response.setHeader('Set-Cookie', `${COOKIE}=${token}; HttpOnly; Max-Age=604800; Path=/; SameSite=Lax`)
  return sendJson(response, 200, { username })
}

function expire(username) {
  for (const [token, user] of sessions) {
    if (user === username) {
      sessions.delete(token)
    }
  }
  for (const run of runs.values()) {
    for (const subscriber of run.subscribers) {
      if (subscriber.user === username) {
        subscriber.end()
      }
    }
  }
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  const path = url.pathname
  let match

  if (request.method === 'GET' && path === '/health') {
    return sendJson(response, 200, { ok: true })
  }
  if (request.method === 'POST' && path === '/auth/login') {
    return login(response, await readBody(request))
  }
  if (request.method === 'POST' && path === '/auth/logout') {
    sessions.delete(tokenOf(request))
    response.writeHead(204, { 'Set-Cookie': `${COOKIE}=""; Max-Age=0; Path=/; HttpOnly; SameSite=Lax` })
    return response.end()
  }
  if (request.method === 'POST' && path === '/__expire') {
    expire(url.searchParams.get('username'))
    return sendJson(response, 200, { ok: true })
  }
  if (request.method === 'GET' && path === '/__ids') {
    const ids = {}
    for (const run of runs.values()) {
      if (!run.live && !run.slow) {
        ids[run.name] = run.id
      }
    }
    return sendJson(response, 200, ids)
  }
  if (request.method === 'POST' && (match = path.match(/^\/__stall\/(\d+)$/))) {
    const run = runs.get(Number(match[1]))
    for (const subscriber of run.subscribers) {
      subscriber.stalled = true
    }
    run.downUntil = Date.now() + Number(url.searchParams.get('down_ms') ?? 0)
    return sendJson(response, 200, { ok: true })
  }
  if (request.method === 'POST' && path === '/__slow') {
    const run = newRun(nextLiveId++, url.searchParams.get('name'), false)
    run.slow = { batches: Number(url.searchParams.get('batches')), gap: Number(url.searchParams.get('gap_ms')) }
    return sendJson(response, 200, { id: run.id })
  }
  // 以下都要登录：没登录 401（前端跳登录页）
  const user = currentUser(request)
  if (!user && (path === '/auth/me' || path.startsWith('/runs'))) {
    return sendJson(response, 401, { detail: '请先登录' })
  }
  if (request.method === 'GET' && path === '/auth/me') {
    return sendJson(response, 200, { username: user })
  }
  if (request.method === 'GET' && path === '/runs') {
    const mine = [...runs.values()].filter((run) => run.owner === user)
    const list = mine.sort((a, b) => b.id - a.id).map((run) => {
      const detail = detailOf(run)
      return { id: run.id, question: detail.question, title: detail.title, status: detail.status, created_at: detail.created_at }
    })
    return sendJson(response, 200, list)
  }
  if (request.method === 'POST' && path === '/runs') {
    const { question } = await readBody(request)
    const name = [...fixtures.keys()].find((key) => fixtures.get(key).detail.question === question)
    if (!name) {
      return sendJson(response, 422, { detail: '假 API 没有录过这个问题' })
    }
    const run = newRun(nextLiveId++, name, true, user)
    run.played = 1
    void play(run, run.segments[0])
    return sendJson(response, 202, { run_id: run.id })
  }
  if ((match = path.match(/^\/runs\/(\d+)(\/regenerate|\/events)?$/))) {
    const run = runs.get(Number(match[1]))
    if (!run || run.owner !== user) {
      return sendJson(response, 404, { detail: 'run 不存在' }) // 别人的题和不存在一样
    }
    if (request.method === 'GET' && !match[2]) {
      return sendJson(response, 200, detailOf(run))
    }
    if (request.method === 'POST' && match[2] === '/regenerate') {
      if (run.running) {
        return sendJson(response, 409, { detail: '这道题还在执行中' })
      }
      if (!run.live || run.played >= run.segments.length) {
        return sendJson(response, 409, { detail: '假 API 没有录过更多的执行' })
      }
      run.played += 1
      void play(run, run.segments[run.played - 1])
      return sendJson(response, 202, { attempt_no: run.played })
    }
    if (request.method === 'GET' && match[2] === '/events') {
      if (Date.now() < run.downUntil) {
        return sendJson(response, 502, { detail: 'API 重启中' })
      }
      const after = Math.max(Number(url.searchParams.get('after') ?? 0), Number(request.headers['last-event-id'] ?? 0))
      return stream(request, response, run, after)
    }
  }
  sendJson(response, 404, { detail: 'not found' })
})

server.listen(PORT, '127.0.0.1', () => console.log(`假 API：http://127.0.0.1:${PORT}，${fixtures.size} 份固定数据`))
