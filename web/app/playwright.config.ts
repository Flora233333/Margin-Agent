/*
 * 端到端测试（Playwright）：真的浏览器打开页面、输入问题、点发送、看结果，和用户的操作一样。
 * 后端换成假 API（e2e/mock-api.mjs），按录好的真实运行回放，不调模型、不起数据库；
 * Vite 开发服务器用另一个端口，/api 转发到假 API。两个服务都由这里自动起、跑完自动关。
 *
 * 运行：npm run e2e（本机用装好的 Chrome；CI 上先 npx playwright install chromium）
 */

import { defineConfig } from '@playwright/test'

const MOCK_PORT = 8100
const WEB_PORT = 5174

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000, // 半死连接那条要等前端 25 秒的心跳超时
  fullyParallel: true, // 每个测试提交自己的题（假 API 里各是一道新题），互不影响
  workers: 3,
  reporter: 'list',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    channel: process.env.CI ? undefined : 'chrome',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node e2e/mock-api.mjs',
      url: `http://127.0.0.1:${MOCK_PORT}/health`,
      env: { MOCK_PORT: String(MOCK_PORT) },
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      env: { MARGIN_API_URL: `http://127.0.0.1:${MOCK_PORT}` },
    },
  ],
})
