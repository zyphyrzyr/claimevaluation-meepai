import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Workbench from './pages/Workbench'
import NewCase from './pages/NewCase'
import Evaluation from './pages/Evaluation'
import DecisionDashboard from './pages/DecisionDashboard'
import MootCourt from './pages/MootCourt'
import MootStandalone from './pages/MootStandalone'
import Report from './pages/Report'
import KnowledgeBase from './pages/KnowledgeBase'
import AdvisorPanel from './components/AdvisorPanel'

const navItems = [
  { to: '/workbench', label: '工作台' },
  { to: '/new', label: '新建案件' },
  { to: '/moot', label: '模拟法庭' },
  { to: '/knowledge', label: '经验库' },
]

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-ink text-white">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <span className="font-medium tracking-wide">
              Soft IP <span className="text-ember">主诉评估</span>
            </span>
            <nav className="flex gap-1 text-sm">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-md transition-colors ${
                      isActive ? 'bg-ink-light text-white' : 'text-white/60 hover:text-white'
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <span className="text-xs text-white/40">v4 · 二维主诉决策模型</span>
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/workbench" replace />} />
          <Route path="/workbench" element={<Workbench />} />
          <Route path="/new" element={<NewCase />} />
          <Route path="/cases/:id/evaluation" element={<Evaluation />} />
          <Route path="/cases/:id/dashboard" element={<DecisionDashboard />} />
          <Route path="/cases/:id/moot" element={<MootCourt />} />
          <Route path="/cases/:id/report" element={<Report />} />
          <Route path="/moot" element={<MootStandalone />} />
          <Route path="/knowledge" element={<KnowledgeBase />} />
        </Routes>
      </main>

      {/* 伴随式追问顾问：案件页面全程悬浮 */}
      <AdvisorPanel />

      <footer className="text-center text-xs text-ink/30 py-4">
        本系统为 AI 辅助评估工具，结果仅供内部决策参考，不构成正式法律意见
      </footer>
    </div>
  )
}
