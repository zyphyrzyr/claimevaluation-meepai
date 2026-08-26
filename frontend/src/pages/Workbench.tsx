import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, CaseItem } from '../api'

const STATUS_LABELS: Record<string, { text: string; cls: string }> = {
  pending: { text: '待评估', cls: 'bg-ink/10 text-ink/60' },
  evaluating: { text: '评估中', cls: 'bg-blue-100 text-blue-700' },
  partial: { text: '部分完成', cls: 'bg-amber-100 text-amber-700' },
  completed: { text: '已完成', cls: 'bg-green-100 text-green-700' },
  blocked: { text: '红线拦截', cls: 'bg-red-100 text-red-700' },
}

export default function Workbench() {
  const [cases, setCases] = useState<CaseItem[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    api.listCases().then(setCases).catch((e) => setError(String(e)))
  }, [])

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-medium">工作台</h1>
          <p className="text-sm text-ink/50 mt-1">能不能诉 · 值不值得诉 · 现在能不能诉</p>
        </div>
        <Link
          to="/new"
          className="bg-ember hover:bg-ember-dark text-white text-sm px-4 py-2 rounded-lg transition-colors"
        >
          + 新建案件
        </Link>
      </div>

      {error && (
        <div className="bg-red-50 text-red-700 text-sm rounded-lg p-4 mb-4">
          后端连接失败：{error}（请确认 uvicorn 已在 8000 端口运行）
        </div>
      )}

      {cases.length === 0 && !error ? (
        <div className="bg-white rounded-xl border border-ink/10 p-16 text-center text-ink/40">
          还没有案件，点击右上角「新建案件」开始第一次主诉评估
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {cases.map((c) => {
            const st = STATUS_LABELS[c.status] ?? STATUS_LABELS.pending
            return (
              <Link
                key={c.id}
                to={c.status === 'pending' ? `/cases/${c.id}/evaluation` : `/cases/${c.id}/dashboard`}
                className="bg-white rounded-xl border border-ink/10 p-5 hover:border-ember/50 hover:shadow-sm transition-all"
              >
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-medium leading-snug">{c.name}</h3>
                  <span className={`text-xs px-2 py-0.5 rounded-full whitespace-nowrap ${st.cls}`}>
                    {st.text}
                  </span>
                </div>
                <div className="mt-3 flex gap-2 text-xs text-ink/50">
                  <span className="bg-ink-pale px-2 py-0.5 rounded">{c.cause_type}</span>
                  <span className="bg-ink-pale px-2 py-0.5 rounded">{c.goal_type}</span>
                </div>
                <div className="mt-3 text-xs text-ink/30">
                  {new Date(c.created_at).toLocaleString('zh-CN')}
                </div>
              </Link>
            )
          })}
        </div>
      )}

      {/* 独立模拟法庭快捷入口（三分钟看产品最抓人的部分） */}
      <Link
        to="/moot"
        className="mt-6 flex items-center justify-between bg-ink text-white rounded-xl p-5 hover:bg-ink-light transition-colors"
      >
        <div>
          <div className="font-medium">独立模拟法庭 · 诉前对抗演练</div>
          <div className="text-sm text-white/50 mt-0.5">
            不建案不评估，输入案情直接开庭——AI 被告当庭抗辩，法官归纳薄弱点
          </div>
        </div>
        <span className="text-ember text-sm font-medium">开庭演练 →</span>
      </Link>
    </div>
  )
}
