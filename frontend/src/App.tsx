import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Workbench from './pages/Workbench'
import Evaluation from './pages/Evaluation'
import DecisionDashboard from './pages/DecisionDashboard'
import MootCourt from './pages/MootCourt'
import MootStandalone from './pages/MootStandalone'
import Report from './pages/Report'
import KnowledgeBase from './pages/KnowledgeBase'
import Settings from './pages/Settings'
import OnePager from './pages/OnePager'
import AdvisorPanel from './components/AdvisorPanel'

const navItems = [
  { to: '/workbench', label: '工作台' },
  { to: '/moot', label: '模拟法庭' },
  { to: '/knowledge', label: '经验库' },
  { to: '/settings', label: '高级设置' },
]

export default function App() {
  return (
    <div className="min-h-screen flex">
      {/* 左侧边栏：与页面同底色，print:hidden 避免打印时带出导航 */}
      <aside className="w-52 flex-shrink-0 bg-canvas text-fg border-r border-line print:hidden">
        <div className="px-6 py-5">
          <span className="font-medium tracking-wide text-fg">
            Soft IP 主诉评估
          </span>
        </div>
        <nav className="flex flex-col gap-1 px-4">
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
            <Route path="/cases/:id/evaluation" element={<Evaluation />} />
            <Route path="/cases/:id/dashboard" element={<DecisionDashboard />} />
            <Route path="/cases/:id/moot" element={<MootCourt />} />
            <Route path="/cases/:id/report" element={<Report />} />
            <Route path="/cases/:id/onepager" element={<OnePager />} />
            <Route path="/moot" element={<MootStandalone />} />
            <Route path="/knowledge" element={<KnowledgeBase />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>

        {/* 伴随式追问顾问：案件页面全程悬浮 */}
        <AdvisorPanel />

        <footer className="text-center text-xs text-muted py-4 print:hidden">
          本系统为 AI 辅助评估工具，结果仅供内部决策参考，不构成正式法律意见
        </footer>
      </div>
    </div>
  )
}
