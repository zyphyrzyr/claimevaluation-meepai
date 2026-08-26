import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, EvalEvent, runEvaluation } from '../api'

const NODE_ORDER = [
  'evidence_review', 'red_gate', 'rights', 'infringement', 'procedure', 'business', 'synthesize',
]
const NODE_LABELS: Record<string, string> = {
  evidence_review: '证据盘点',
  red_gate: '硬门禁检查',
  rights: '权利基础',
  infringement: '侵权认定',
  procedure: '诉讼程序',
  business: '业务预期',
  synthesize: '决策合成',
}

type NodeState = 'waiting' | 'running' | 'ok' | 'failed' | 'blocked' | 'partial'

export default function Evaluation() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [states, setStates] = useState<Record<string, NodeState>>({})
  const [log, setLog] = useState<string[]>([])
  const [finished, setFinished] = useState<string>('')
  const [error, setError] = useState('')
  const started = useRef(false)

  useEffect(() => {
    if (!id || started.current) return
    started.current = true

    const onEvent = (e: EvalEvent) => {
      if (e.event === 'node_started') {
        setStates((s) => ({ ...s, [e.node]: 'running' }))
        setLog((l) => [...l, `开始：${e.label}`])
      } else if (e.event === 'node_finished') {
        setStates((s) => ({ ...s, [e.node]: (e.status as NodeState) ?? 'ok' }))
        setLog((l) => [...l, `完成：${e.label}（${e.status}）`])
      } else if (e.event === 'flow_blocked') {
        setLog((l) => [...l, '⚠ 命中程序性红线，流程提前终止'])
      } else if (e.event === 'flow_finished') {
        setFinished(e.status ?? '')
        setLog((l) => [...l, '评估流程结束'])
      } else if (e.event === 'flow_error') {
        setError(e.error ?? '未知错误')
      }
    }

    runEvaluation(id, onEvent).catch((e) => setError(String(e)))
  }, [id])

  const stateIcon = (s?: NodeState) => {
    switch (s) {
      case 'running': return <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
      case 'ok': return <span className="w-2.5 h-2.5 rounded-full bg-green-500" />
      case 'failed': return <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
      case 'blocked': return <span className="w-2.5 h-2.5 rounded-full bg-red-700" />
      case 'partial': return <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
      default: return <span className="w-2.5 h-2.5 rounded-full bg-ink/15" />
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-medium mb-1">评估分析</h1>
      <p className="text-sm text-ink/50 mb-6">证据盘点 → 硬门禁 → 法律可行性 → 业务预期 → 决策合成</p>

      <div className="bg-white rounded-xl border border-ink/10 p-6">
        <div className="space-y-3">
          {NODE_ORDER.map((node) => (
            <div key={node} className="flex items-center gap-3">
              {stateIcon(states[node])}
              <span className={`text-sm ${states[node] ? 'text-ink' : 'text-ink/40'}`}>
                {NODE_LABELS[node]}
              </span>
              {states[node] === 'failed' && (
                <span className="text-xs text-red-600">失败（不影响其他维度，总分将标注未完成）</span>
              )}
              {states[node] === 'blocked' && (
                <span className="text-xs text-red-700 font-medium">命中红线</span>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 bg-ink rounded-xl p-4 text-xs text-white/70 font-mono h-40 overflow-y-auto">
        {log.map((line, i) => <div key={i}>{line}</div>)}
        {error && <div className="text-red-400">错误：{error}</div>}
      </div>

      {finished && (
        <button
          onClick={() => navigate(`/cases/${id}/dashboard`)}
          className="mt-6 w-full bg-ember hover:bg-ember-dark text-white py-3 rounded-lg text-sm font-medium transition-colors"
        >
          查看决策仪表盘 →
        </button>
      )}
    </div>
  )
}
