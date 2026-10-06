/*
 * 输入框：只有问题，提交到 POST /runs。答案格式由后端理解题目后判断（PLAN §5.8 ①），用户不用选。
 *
 * 幂等键（Idempotency-Key）：一次“想提交这道题”对应一个键，保存在 keyRef 里。
 *   - 请求失败（断网、API 重启）后用户再点发送：还是同一个键，后端最多只建一个 run；
 *   - 提交成功后才换新键，下一道题是新的提交。
 * 提交过程中按钮禁用，连点两下也只发一次请求。
 */

import { useRef, useState } from 'react'
import { ApiError, createRun } from '../api'
import { Icon } from './Icons'

export function Composer({ onCreated }: { onCreated: (runId: number) => void }) {
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const keyRef = useRef(crypto.randomUUID())

  const canSubmit = question.trim() !== '' && !submitting

  async function submit() {
    if (!canSubmit) {
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const { run_id } = await createRun({ question: question.trim() }, keyRef.current)
      keyRef.current = crypto.randomUUID()
      setQuestion('')
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
