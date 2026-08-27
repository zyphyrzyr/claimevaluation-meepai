import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, EvalEvent, KnowledgeEntryItem, knowledgeApi, runEvaluation } from '../api'

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
type Phase = 'prep' | 'running' | 'done'

export default function Evaluation() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('prep')
  const [states, setStates] = useState<Record<string, NodeState>>({})
  const [log, setLog] = useState<string[]>([])
  const [finished, setFinished] = useState<string>('')
  const [error, setError] = useState('')

  // 准备阶段：知识注入
  const [caseEntries, setCaseEntries] = useState<KnowledgeEntryItem[]>([])
  const [globalEntries, setGlobalEntries] = useState<KnowledgeEntryItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [viewpoints, setViewpoints] = useState<string[]>([])
  const [injectedInfo, setInjectedInfo] = useState('')
  const [evaluated, setEvaluated] = useState(false)

  useEffect(() => {
    if (!id) return
    knowledgeApi.caseEntries(id).then(setCaseEntries).catch(() => {})
    knowledgeApi.entries({ scope: 'global' }).then(setGlobalEntries).catch(() => {})
    api.caseDetail(id).then((d) => {
      setViewpoints(d.context?.user_viewpoints ?? [])
      setEvaluated(Boolean(d.context?.scores?.final != null))
    }).catch(() => {})
  }, [id])

  const toggle = (entryId: string) => {
    setSelected((s) => {
      const copy = new Set(s)
      copy.has(entryId) ? copy.delete(entryId) : copy.add(entryId)
      return copy
    })
  }

  const start = async () => {
    if (!id) return
    setError('')
    setInjectedInfo('')
    try {
      if (selected.size > 0) {
        const r = await knowledgeApi.inject(id, Array.from(selected))
        setInjectedInfo(`已注入 ${r.injected} 条参考材料到全部评估节点`)
      } else {
        // 未勾选 = 清空注入（保证复现口径一致）
        await knowledgeApi.inject(id, [])
      }
    } catch (e) {
      setError(`材料注入失败：${e}`)
      return
    }
    setPhase('running')
    setLog(['评估流程启动…'])
    runEvaluation(id, onEvent).catch((e) => setError(String(e)))
  }

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
      setPhase('done')
      setLog((l) => [...l, '评估流程结束'])
    } else if (e.event === 'flow_error') {
      setError(e.error ?? '未知错误')
    }
  }

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

  const entryRow = (e: KnowledgeEntryItem, tag: string) => (
    <label key={e.id} className="flex items-start gap-2.5 py-1.5 cursor-pointer group">
      <input
        type="checkbox"
        checked={selected.has(e.id)}
        onChange={() => toggle(e.id)}
        className="mt-0.5 accent-[#d65938]"
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm text-ink group-hover:text-ember truncate">{e.title}</div>
        <div className="text-[11px] text-ink/40">
          {tag} · {e.source_type_label} · {e.chunk_count} 块
        </div>
      </div>
    </label>
  )

  // ------------------------------------------------- 准备阶段：知识注入
  if (phase === 'prep') {
    return (
      <div className="max-w-3xl">
        <h1 className="text-xl font-medium mb-1">评估准备 · 参考材料注入</h1>
        <p className="text-sm text-ink/50 mb-6">
          勾选的知识库条目将注入全部 LLM 评估节点（评分链路手动勾选，保证结果可复现）；
          右下角追问顾问与模拟法庭则会按需自动召回
          {evaluated && <span className="text-amber-700">（本案已有评估结果，重新评估将覆盖）</span>}
        </p>

        {viewpoints.length > 0 && (
          <div className="bg-white rounded-xl border border-ink/10 p-5 mb-4">
            <h2 className="text-sm font-medium mb-2">已注入观点（建案时录入）</h2>
            {viewpoints.map((v, i) => (
              <div key={i} className="text-sm text-ink/70">· {v}</div>
            ))}
          </div>
        )}

        <div className="grid md:grid-cols-2 gap-4">
          <div className="bg-white rounded-xl border border-ink/10 p-5">
            <h2 className="text-sm font-medium mb-1">本案材料库（case 隔离）</h2>
            <p className="text-[11px] text-ink/40 mb-2">建案时证据文本已自动入库</p>
            {caseEntries.length ? caseEntries.map((e) => entryRow(e, '案件材料'))
              : <p className="text-sm text-ink/40 py-2">暂无（可在建案时粘贴证据文本）</p>}
          </div>
          <div className="bg-white rounded-xl border border-ink/10 p-5">
            <h2 className="text-sm font-medium mb-1">全局经验库（跨案共享）</h2>
            <p className="text-[11px] text-ink/40 mb-2">
              <Link to="/knowledge" className="text-ember hover:underline">去经验库管理 →</Link>
            </p>
            {globalEntries.length ? globalEntries.map((e) => entryRow(e, '经验库'))
              : <p className="text-sm text-ink/40 py-2">暂无经验条目</p>}
          </div>
        </div>

        {error && <div className="bg-red-50 text-red-700 rounded-lg p-3 text-sm mt-4">{error}</div>}

        <button
          onClick={start}
          className="mt-6 w-full bg-ember hover:bg-ember-dark text-white py-3 rounded-lg text-sm font-medium transition-colors"
        >
          开始评估（已勾选 {selected.size} 条参考材料）→
        </button>
      </div>
    )
  }

  // ------------------------------------------------- 运行/完成阶段
  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-medium mb-1">评估分析</h1>
      <p className="text-sm text-ink/50 mb-6">证据盘点 → 硬门禁 → 法律可行性 → 业务预期 → 决策合成</p>
      {injectedInfo && (
        <div className="bg-blue-50 text-blue-800 rounded-lg px-4 py-2 text-xs mb-4">{injectedInfo}</div>
      )}

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
