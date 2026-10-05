/*
 * 执行失败时的说明（attempt_failed 事件的 error）。错误码的来源：
 *   llm_http_502 等   worker 调模型网关时收到的 HTTP 错误（worker._describe）
 *   ReadTimeout 等    worker 调模型时的其他异常，记的是异常类名（不记 str(exc)，里面有网关地址）
 *   lease_expired     worker 没有按时续租（崩溃或卡死），dispatcher 巡检判为失败（lease.expire_leases）
 *   delivery_lost     多次重新投递后仍没有 worker 领取（dispatcher.requeue_lost，M1.5）
 */

import { Icon } from './Icons'

function failureMessage(error: string | null): string {
  if (error === 'lease_expired') {
    return '执行中断：负责这道题的 worker 没有按时报告进度（可能已崩溃），这次执行判为失败。'
  }
  if (error === 'delivery_lost') {
    return '任务没能交给 worker：多次重新投递后仍没有 worker 领取。'
  }
  const http = /^llm_http_(\d+)$/.exec(error ?? '')
  if (http) {
    return `模型网关返回错误（HTTP ${http[1]}），这次执行没有完成。`
  }
  return `执行出错（${error}），这次执行没有完成。`
}

export function Failure({ error }: { error: string | null }) {
  return (
    <div className="failure" role="alert">
      <Icon name="alert" />
      <div>
        <p>{failureMessage(error)}</p>
        <p className="failure-hint">已完成的步骤保留在上面。可以点右上角“重新生成”再试一次。</p>
      </div>
    </div>
  )
}
