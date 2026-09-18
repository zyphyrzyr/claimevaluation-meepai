import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Workbench from './pages/Workbench'
import CaseWorkbench from './pages/CaseWorkbench'
import KnowledgeBase from './pages/KnowledgeBase'
import Settings from './pages/Settings'
import AdvisorPanel from './components/AdvisorPanel'
import RunModeBadge from './components/RunModeBadge'
import SidebarUser from './components/SidebarUser'
import LoginModal from './components/LoginModal'
import { useAuth } from './auth/AuthProvider'
import { humanError } from './api'

// 模拟法庭不再有全局入口、也不再有独立路由：两条入口（案件详情「仅开始模拟法庭」/
// 评估详情的模拟法庭轴）全部落在个案工作台内部，
// 用 ?tab=run&axis=eval-moot 定位、就地开庭。旧书签 /cases/:id/moot 会命中兜底路由
// 回到案件列表——那是刻意的：那一页会把整个页面刷成暗色，与评估详情割裂。
// 「高级设置」同理移出导航：那一页是部署方的运维台（供应商切换 + 连通性自检），
// 摆给客户看只会暴露成本档位与密钥掩码。它与 /settings 路由都还在，知道地址就能进。
const navItems = [
  { to: '/workbench', label: '案件列表' },
  { to: '/knowledge', label: '个人知识库' },
]

export default function App() {
  const auth = useAuth()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const handleLogin = async (email: string, password: string) => {
    setBusy(true)
    setError('')
    try {
      await auth.login(email, password)
      auth.closeLogin()
    } catch (e) {
      // 登录端的 401 要显示出来（「邮箱或密码不正确」），所以这里不能用
      // humanError——它把 401 翻成空串是为了避开拦截器弹框，而登录框
      // 正是用户此刻要看错误的地方。
      setError(loginMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const handleRegister = async (email: string, password: string, displayName: string) => {
    setBusy(true)
    setError('')
    try {
      await auth.register(email, password, displayName)
      auth.closeLogin()
    } catch (e) {
      setError(loginMessage(e))
    } finally {
      setBusy(false)
    }
  }

  // 每次开框都清空上一次的错误：留着上次的「密码错误」会让人以为这次也没成功
  const openLogin = (reason?: string) => {
    setError('')
    auth.openLogin(reason)
  }

  return (
    <div className="min-h-screen flex">
      {/* 左侧边栏：与页面同底色，print:hidden 避免打印时带出导航 */}
      <aside className="w-[15.5rem] flex-shrink-0 bg-canvas text-fg print:hidden flex flex-col sticky top-0 h-screen">
        <div className="px-6 py-5">
          <span className="text-xl font-medium text-fg">
            诉算·主诉评估
          </span>
        </div>
        <nav className="mt-[25vh] flex flex-col gap-1 px-4">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive ? 'text-fg font-medium' : 'text-muted hover:text-fg'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* 左下角登录态：块自己用 top-[75vh] 定位在侧栏 3/4 高度处
            （aside 是 sticky，正是它的包含块），不占文档流、也不贴底 */}
        <SidebarUser
          user={auth.user}
          loading={auth.loading}
          onOpenLogin={() => openLogin()}
          onLogout={auth.logout}
        />
      </aside>

      {/* 右侧内容区 */}
      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 w-full px-8 pt-5 pb-8">
          <Routes>
            <Route path="/" element={<Navigate to="/workbench" replace />} />
            <Route path="/workbench" element={<Workbench />} />
            <Route path="/cases/:id" element={<CaseWorkbench />} />
            {/* /cases/:id/moot 已下架：庭审并入个案工作台的「评估详情 - 模拟法庭」轴 */}
            <Route path="/knowledge" element={<KnowledgeBase />} />
            {/* 保留但不在导航中：部署方运维入口（供应商切换 + 连通性自检） */}
            <Route path="/settings" element={<Settings />} />
            {/* 兜底：/moot 独立演练页已下架，旧书签或错 URL 不再渲染成空白内容区，统一跳回案件列表 */}
            <Route path="*" element={<Navigate to="/workbench" replace />} />
          </Routes>
        </main>

        {/* 伴随式追问顾问：案件页面全程悬浮 */}
        <AdvisorPanel />

        <footer className="text-center text-xs text-muted py-4 space-y-1 print:hidden">
          <RunModeBadge />
          <div>本系统为 AI 辅助评估工具，结果仅供内部决策参考，不构成正式法律意见</div>
        </footer>
      </div>

      <LoginModal
        open={auth.loginOpen}
        onClose={auth.closeLogin}
        reason={auth.loginReason}
        busy={busy}
        error={error}
        onLogin={handleLogin}
        onRegister={handleRegister}
      />
    </div>
  )
}

/**
 * 登录/注册的错误文案。
 *
 * 与 humanError 的区别只有一个：401 也要原文显示。
 * 未登录撞 401 由拦截器弹框、表单不必再喊；但在**登录框里**，
 * 401 就是「邮箱或密码不正确」，把它藏起来用户只会看到按钮没反应。
 */
function loginMessage(e: unknown): string {
  const msg = humanError(e)
  if (msg) return msg
  const raw = e instanceof Error ? e.message : String(e)
  const body = raw.replace(/^\d+:\s*/, '')
  try {
    const parsed = JSON.parse(body)
    if (parsed?.detail) return String(parsed.detail)
  } catch {
    /* 不是 JSON 就原样用 */
  }
  return body || '登录失败，请重试'
}
