/*
 * 输入框：问题 + 答案格式（+ 选择题的四个选项），提交到 POST /runs。
 *
 * 幂等键（Idempotency-Key）：一次“想提交这道题”对应一个键，保存在 keyRef 里。
 *   - 请求失败（断网、API 重启）后用户再点发送：还是同一个键，后端最多只建一个 run；
 *   - 提交成功后才换新键，下一道题是新的提交。
 * 提交过程中按钮禁用，连点两下也只发一次请求。
 */

import { useRef, useState } from 'react'
import { type AnswerFormat, ApiError, createRun, type OptionKey } from '../api'
import { Icon } from './Icons'

const FORMATS: { value: AnswerFormat; label: string }[] = [
  { value: 'num', label: '数值' },
  { value: 'pct', label: '百分比' },
  { value: 'tf', label: '判断' },
  { value: 'mcq', label: '单选' },
  { value: 'multi', label: '多选' },
  { value: 'date', label: '日期' },
  { value: 'rank', label: '排序' },
  { value: 'text', label: '文本' },
]
const OPTION_KEYS: OptionKey[] = ['A', 'B', 'C', 'D']
const EMPTY_OPTIONS: Record<OptionKey, string> = { A: '', B: '', C: '', D: '' }

export function Composer({ onCreated }: { onCreated: (runId: number) => void }) {
  const [question, setQuestion] = useState('')
  const [format, setFormat] = useState<AnswerFormat>('num')
  const [options, setOptions] = useState(EMPTY_OPTIONS)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const keyRef = useRef(crypto.randomUUID())

  const withOptions = format === 'mcq' || format === 'multi'
  const canSubmit = question.trim() !== '' && !submitting

  async function submit() {
    if (!canSubmit) {
      return
    }
    setSubmitting(true)
    setError('')
    try {
      // 只提交填了的选项；后端校验选项的键只能是 A–D
      const filled = Object.fromEntries(
        Object.entries(options).filter(([, text]) => text.trim() !== ''),
      )
      const { run_id } = await createRun(
        { question: question.trim(), options: withOptions ? filled : null, answer_format: format },
        keyRef.current,
      )
      keyRef.current = crypto.randomUUID()
      setQuestion('')
      setOptions(EMPTY_OPTIONS)
      onCreated(run_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="composer-wrap">
      {error && (
        <p className="form-error" role="alert">
          <Icon name="alert" />
          {error}
        </p>
      )}
      <div className="composer">
        <textarea
          rows={1}
          placeholder="输入一个关于债券募集说明书的问题…"
          aria-label="输入问题"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送、Shift+Enter 换行。isComposing：中文输入法正在拼字时按的 Enter 是“上屏”，不能当发送
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              void submit()
            }
          }}
        />
        <div className="composer-tools collapsible">
          <div>
            {withOptions && (
              <div className="composer-options">
                {OPTION_KEYS.map((key) => (
                  <label key={key}>
                    <span className="num">{key}</span>
                    <input
                      value={options[key]}
                      onChange={(e) => setOptions({ ...options, [key]: e.target.value })}
                      aria-label={`选项 ${key}`}
                    />
                  </label>
                ))}
              </div>
            )}
            <div className="composer-bar">
              <label className="chip">
                答案格式
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value as AnswerFormat)}
                  aria-label="答案格式"
                >
                  {FORMATS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </div>
        <button
          className="send"
          type="button"
          aria-label="发送"
          disabled={!canSubmit}
          onClick={() => void submit()}
        >
          <Icon name="up" />
        </button>
      </div>
    </div>
  )
}
