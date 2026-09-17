import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Workbench from './pages/Workbench'
import CaseWorkbench from './pages/CaseWorkbench'
import MootCourt from './pages/MootCourt'
import KnowledgeBase from './pages/KnowledgeBase'
import Settings from './pages/Settings'
import AdvisorPanel from './components/AdvisorPanel'
import RunModeBadge from './components/RunModeBadge'

// 模拟法庭不再有全局入口：只能从个案工作台启动（案件详情「仅开始模拟法庭」/
// 评估详情与评估结果的「启动模拟法庭」），三条入口都落在 /cases/:id/moot。
// 「高级设置」同理移出导航：那一页是部署方的运维台（供应商切换 + 连通性自检），
// 摆给客户看只会暴露成本档位与密钥掩码。它与 /settings 路由都还在，知道地址就能进。
const navItems = [
  { to: '/workbench', label: '案件列表' },
  { to: '/knowledge', label: '经验库' },
]

export default function App() {
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
      </aside>

      {/* 右侧内容区 */}
      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 w-full px-8 pt-5 pb-8">
          <Routes>
            <Route path="/" element={<Navigate to="/workbench" replace />} />
            <Route path="/workbench" element={<Workbench />} />
            <Route path="/cases/:id" element={<CaseWorkbench />} />
            <Route path="/cases/:id/moot" element={<MootCourt />} />
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
    </div>
  )
}
