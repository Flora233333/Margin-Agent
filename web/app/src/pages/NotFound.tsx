import { navigate } from '../router'

export function NotFound({ message = '这个地址没有对应的页面。' }: { message?: string }) {
  return (
    <main className="main">
      <div className="thread">
        <div className="empty">
          <p className="empty-code num">404</p>
          <h1 className="empty-title">找不到</h1>
          <p>{message}</p>
          <button className="btn-new" type="button" onClick={() => navigate('/')}>
            回到新提问
          </button>
        </div>
      </div>
    </main>
  )
}
