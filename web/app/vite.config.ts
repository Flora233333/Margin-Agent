import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 开发时前端（5173）和 API（8000）不同源。浏览器只请求同源的 /api/...，
    // 由 Vite 转发给 API 并去掉 /api 前缀，这样不用给 API 开 CORS。
    // 上线后同样的事由 Nginx 做（M6），前端代码不用改。
    // SSE 也走这条转发：它只是一个不结束的 HTTP 响应，Vite 收到一段就转一段，不会攒着。
    // 端到端测试把它指到假 API（e2e/mock-api.mjs，见 playwright.config.ts）
    proxy: {
      '/api': {
        target: process.env.MARGIN_API_URL ?? 'http://127.0.0.1:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    environment: 'node', // 只测纯函数（不渲染组件），不需要模拟浏览器
    include: ['src/**/*.test.ts'], // e2e/ 下是 Playwright 的测试，由 npm run e2e 跑
  },
})
