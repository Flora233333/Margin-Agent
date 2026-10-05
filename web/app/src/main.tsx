import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// 样式顺序：设计变量 → 公共结构 → 简洁风覆盖 → 设计稿里没有、应用才需要的部分
import './styles/tokens.css'
import './styles/base.css'
import './styles/clean.css'
import './styles/app.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
