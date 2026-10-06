/*
 * 录制端到端测试用的固定数据：从正在运行的 API 取一道已结束的题（GET /runs/{id}）和它的全部持久事件
 * （SSE 从 after=0 收到关闭），存成 e2e/fixtures/<名字>.json。假 API（mock-api.mjs）按它回放。
 *
 * 用法（先起好 compose 里的 api）：
 *   node e2e/record.mjs num_short=93 text_long=69 ...
 *
 * 实时片段（delta）不落库，录不到；回放时假 API 用每一步的完整思考、撰写好的回答切成片段补上。
 */

import { writeFileSync } from 'node:fs'

const API = process.env.MARGIN_API ?? 'http://127.0.0.1:8000'

async function events(runId) {
  const response = await fetch(`${API}/runs/${runId}/events?after=0`)
  const text = await response.text() // 已结束的题：服务端补发完就关闭，读到结尾即可
  const parsed = []
  for (const block of text.split('\n\n')) {
    const fields = Object.fromEntries(block.split('\n').filter(Boolean).map((line) => {
      const at = line.indexOf(': ')
      return [line.slice(0, at), line.slice(at + 2)]
    }))
    if (fields.id) {
      parsed.push({ seq: Number(fields.id), type: fields.event, data: JSON.parse(fields.data) })
    }
  }
  return parsed
}

for (const arg of process.argv.slice(2)) {
  const [name, runId] = arg.split('=')
  const detail = await (await fetch(`${API}/runs/${runId}`)).json()
  if (detail.status !== 'completed' && detail.status !== 'failed') {
    throw new Error(`run ${runId} 还没结束`)
  }
  // 每一步的内容事件里都有，详情只留题目和每次执行的时间（页面用它算耗时），文件小一半
  detail.attempts = detail.attempts.map((attempt) => ({ ...attempt, steps: [] }))
  const fixture = { source_run: Number(runId), detail, events: await events(runId) }
  writeFileSync(new URL(`./fixtures/${name}.json`, import.meta.url), JSON.stringify(fixture, null, 1) + '\n')
  console.log(name, `run ${runId}`, `${fixture.events.length} 个事件`)
}
