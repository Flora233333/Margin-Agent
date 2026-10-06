/*
 * 后端接口的封装：每个函数对应 src/margin/api.py 里的一个路由，类型和那边的 Pydantic 模型一一对应。
 * 组件里不直接写 fetch，出错统一变成 ApiError（带 HTTP 状态码和一句能给用户看的中文说明）。
 *
 * 地址都以 /api 开头：开发时由 Vite 转发到 API（见 vite.config.ts），上线后由 Nginx 转发。
 */

export type AnswerFormat = 'num' | 'pct' | 'tf' | 'mcq' | 'multi' | 'date' | 'rank' | 'text'

export const FORMAT_LABEL: Record<AnswerFormat, string> = {
  num: '数值', pct: '百分比', tf: '判断', mcq: '单选', multi: '多选', date: '日期', rank: '排序', text: '文本',
}
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed'

/** 页面只提交问题：答案格式由后端理解题目后判断，选项只给评测回放用（PLAN §5.8） */
export interface RunCreate {
  question: string
}

export interface RunSummary {
  id: number
  question: string
  title: string | null // 理解题目后的标题；还没理解完时为空，显示问题原句
  status: RunStatus
  created_at: string
}

export interface StepOut {
  step_no: number
  tool_name: string
  arguments: string
  result: Record<string, unknown>
  reasoning: string | null
  llm_ms: number
}

export interface AttemptOut {
  attempt_no: number
  trigger: string
  status: string
  final: Record<string, unknown> | null
  violation: string | null
  error: string | null
  started_at: string | null
  finished_at: string | null
  steps: StepOut[]
}

export interface RunDetail {
  id: number
  question: string
  title: string | null
  answer_label: string | null // 结论旁边的一行说明
  options: Record<string, string> | null
  answer_format: AnswerFormat | null // 调用方给定的格式（评测回放）；页面上提交的题为空
  guessed_format: AnswerFormat | null // 理解题目猜的格式
  model: string
  status: RunStatus
  created_at: string
  attempts: AttemptOut[]
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// 422 时 FastAPI 返回的 detail：每个不合格的字段一条，loc 是字段路径，例如 ["body", "question"]
interface FieldError {
  loc: (string | number)[]
  msg: string
  type: string
}

const FIELD_NAMES: Record<string, string> = {
  question: '问题',
  options: '选项',
  answer_format: '答案格式',
  'idempotency-key': '幂等键',
}

const ERROR_TYPES: Record<string, string> = {
  missing: '不能为空',
  string_too_short: '不能为空',
  string_too_long: '太长了',
  literal_error: '取值不在允许的范围内',
  extra_forbidden: '是多余的字段',
}

/** 把后端返回的错误变成一句中文。422 的 detail 是字段错误列表，其余是一句话（见 api.py 的异常处理）。 */
export function describeError(status: number, detail: unknown): string {
  if (status === 422 && Array.isArray(detail)) {
    return (detail as FieldError[])
      .map((e) => {
        const field = String(e.loc[e.loc.length - 1])
        const name = FIELD_NAMES[field] ?? field
        let reason = ERROR_TYPES[e.type] ?? e.msg
        if (e.type === 'string_too_long' && field === 'question') {
          reason = '最多 2000 字'
        }
        return `${name}${reason}`
      })
      .join('；')
  }
  if (typeof detail === 'string') {
    return detail
  }
  return `请求失败（HTTP ${status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, init)
  } catch {
    // fetch 只在“请求根本没发出去或没收到响应”时抛异常（断网、API 没启动）；4xx/5xx 不会抛
    throw new ApiError(0, '连不上服务器，请稍后重试')
  }
  if (!response.ok) {
    // Vite 转发失败（API 没启动）时返回的 502/504 不是 JSON，读不出 detail 就用通用说明
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, describeError(response.status, body?.detail))
  }
  return response.json() as Promise<T>
}

/**
 * 提交一道题。idempotencyKey 由调用方生成并保管：网络出错后用户再点一次，用的还是同一个键，
 * 后端只会建一个 run（runs.create_run 的 ON CONFLICT）；提交成功后调用方再换一个新键。
 */
export function createRun(body: RunCreate, idempotencyKey: string): Promise<{ run_id: number }> {
  return request('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(body),
  })
}

export function listRuns(): Promise<RunSummary[]> {
  return request('/runs')
}

export function getRun(runId: number): Promise<RunDetail> {
  return request(`/runs/${runId}`)
}

export function regenerate(runId: number): Promise<{ attempt_no: number }> {
  return request(`/runs/${runId}/regenerate`, { method: 'POST' })
}

/** SSE 地址。after 是已经收到的最后一个 seq，服务端从它之后补发（EventSource 不能自己设请求头）。 */
export function eventsUrl(runId: number, after: number): string {
  return `/api/runs/${runId}/events?after=${after}`
}
