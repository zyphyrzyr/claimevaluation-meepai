import { useEffect, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, EvalEvent, KnowledgeEntryItem, knowledgeApi, runEvaluation } from '../api'
import { cn } from '../lib/utils'
import { tierOf, type Thresholds, type NodeState } from '../lib/tiers'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { StatusDot } from '../components/ui/StatusDot'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { StepperNode } from '../components/ui/StepperNode'
import { ConclusionCard } from '../components/ui/ConclusionCard'

// 流程顺序与后端 NODE_ORDER 对齐（orchestrator.py）
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
  // 业务子维度（仅存在于 dimension_results，不在 NODE_ORDER）
  damages: '判赔规模',
  recovery: '回款能力',
  precedent: '判例价值',
}
const RERUNNABLE = new Set(['evidence_review', 'rights', 'infringement', 'procedure', 'business'])

// 分轴呈现：让「每个环节的信息与结论」沿横轴分组清晰铺开
const AXES: { axis: string; nodes: string[] }[] = [
  { axis: '前置盘点', nodes: ['evidence_review', 'red_gate'] },
  { axis: '法律可行性轴', nodes: ['rights', 'infringement', 'procedure'] },
  { axis: '业务预期轴', nodes: ['business'] },
  { axis: '决策合成', nodes: ['synthesize'] },
]

type Phase = 'prep' | 'running' | 'done'

const SEV_LABEL: Record<string, string> = { pass: '通过', warning: '警示', block: '拦截' }
const RISK_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低', none: '无' }
function elemColor(s?: string) {
  if (s === '满足') return 'bg-[var(--success-soft)] text-[var(--success)]'
  if (s === '存疑') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  if (s === '不满足') return 'bg-[var(--danger-soft)] text-[var(--danger)]'
  return 'bg-surface text-muted'
}
function riskColor(l?: string) {
  if (l === 'high') return 'bg-[var(--danger-soft)] text-[var(--danger)]'
  if (l === 'medium') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  if (l === 'low') return 'bg-[var(--success-soft)] text-[var(--success)]'
  return 'bg-surface text-muted'
}
function scaleLabel(s?: string) {
  return s === 'high' ? '高' : s === 'medium' ? '中' : s === 'low' ? '低' : (s ?? '—')
}

export default function Evaluation() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('prep')
  const [states, setStates] = useState<Record<string, NodeState>>({})
  const [finished, setFinished] = useState<string>('')
  const [error, setError] = useState('')

  // 准备阶段：知识注入
  const [caseEntries, setCaseEntries] = useState<KnowledgeEntryItem[]>([])
  const [globalEntries, setGlobalEntries] = useState<KnowledgeEntryItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [viewpoints, setViewpoints] = useState<string[]>([])
  const [injectedInfo, setInjectedInfo] = useState('')
  const [evaluated, setEvaluated] = useState(false)

  // 运行/完成阶段：逐节点拉取的完整结果
  const [result, setResult] = useState<any>(null)

  // 节点级重跑
  const [rerunTarget, setRerunTarget] = useState<string | null>(null)
  const [rerunGuidance, setRerunGuidance] = useState('')
  const [rerunBusy, setRerunBusy] = useState(false)

  const thresholds: Thresholds =
    result?.thresholds ?? { go: 78, patch: 62, quadrant_mid: 78, power_mean_p: -0.5 }
  const goalType: string = result?.goal_type ?? '要钱'
  const blocked = Boolean(result?.dimension_results?.red_gate?.result?.blocked)

  useEffect(() => {
    if (!id) return
    knowledgeApi.caseEntries(id).then(setCaseEntries).catch(() => {})
    knowledgeApi.entries({ scope: 'global' }).then(setGlobalEntries).catch(() => {})
    api.caseDetail(id).then((d) => {
      setViewpoints(d.context?.user_viewpoints ?? [])
      setEvaluated(Boolean(d.context?.scores?.final != null))
    }).catch(() => {})
  }, [id])

  // 进入运行态即拉一次结果（断线重连/重看时也能先有数据）
  const refreshResult = () => {
    if (!id) return
    api.result(id).then(setResult).catch(() => {})
  }

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
        await knowledgeApi.inject(id, [])
      }
    } catch (e) {
      setError(`材料注入失败：${e}`)
      return
    }
    setStates({})
    setPhase('running')
    refreshResult()
    runEvaluation(id, onEvent).catch((e) => setError(String(e)))
  }

  const onEvent = (e: EvalEvent) => {
    if (e.event === 'node_started') {
      setStates((s) => ({ ...s, [e.node]: 'running' }))
    } else if (e.event === 'node_finished') {
      setStates((s) => ({ ...s, [e.node]: (e.status as NodeState) ?? 'ok' }))
      refreshResult() // 每个节点完成即回填详情（P1-3）
    } else if (e.event === 'flow_blocked') {
      refreshResult()
    } else if (e.event === 'flow_finished') {
      setFinished(e.status ?? '')
      setPhase('done')
      refreshResult()
    } else if (e.event === 'flow_error') {
      setError(e.error ?? '未知错误')
    }
  }

  const doRerun = async (node: string) => {
    if (!id) return
    setRerunBusy(true)
    setError('')
    try {
      await api.rerun(id, node, rerunGuidance)
      await refreshResult()
    } catch (e) {
      setError(String(e))
    } finally {
      setRerunBusy(false)
      setRerunTarget(null)
      setRerunGuidance('')
    }
  }

  // ---------------------------------------------------------------- 准备阶段：知识注入
  if (phase === 'prep') {
    const entryRow = (e: KnowledgeEntryItem, tag: string) => (
      <label key={e.id} className="flex items-start gap-2.5 py-1.5 cursor-pointer group">
        <input
          type="checkbox"
          checked={selected.has(e.id)}
          onChange={() => toggle(e.id)}
          className="mt-0.5 accent-brand"
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-fg truncate">{e.title}</div>
          <div className="text-[11px] text-muted">
            {tag} · {e.source_type_label} · {e.chunk_count} 块
          </div>
        </div>
      </label>
    )
    return (
      <div className="max-w-3xl">
        <h1 className="text-xl font-medium mb-1">评估准备 · 参考材料注入</h1>
        <p className="text-sm text-muted mb-6">
          勾选的知识库条目将注入全部 LLM 评估节点（评分链路手动勾选，保证结果可复现）；
          右下角追问顾问与模拟法庭则会按需自动召回
          {evaluated && <span className="text-[var(--warning)]">（本案已有评估结果，重新评估将覆盖）</span>}
        </p>

        {viewpoints.length > 0 && (
          <Card className="p-5 mb-4">
            <h2 className="text-sm font-medium mb-2">已注入观点（建案时录入）</h2>
            {viewpoints.map((v, i) => (
              <div key={i} className="text-sm text-muted">· {v}</div>
            ))}
          </Card>
        )}

        <div className="grid md:grid-cols-2 gap-4">
          <Card className="p-5">
            <h2 className="text-sm font-medium mb-1">本案材料库（case 隔离）</h2>
            <p className="text-[11px] text-muted mb-2">建案时证据文本已自动入库</p>
            {caseEntries.length ? caseEntries.map((e) => entryRow(e, '案件材料'))
              : <p className="text-sm text-muted py-2">暂无（可在建案时粘贴证据文本）</p>}
          </Card>
          <Card className="p-5">
            <h2 className="text-sm font-medium mb-1">全局经验库（跨案共享）</h2>
            <p className="text-[11px] text-muted mb-2">
              <Link to="/knowledge" className="text-brand hover:underline">去经验库管理 →</Link>
            </p>
            {globalEntries.length ? globalEntries.map((e) => entryRow(e, '经验库'))
              : <p className="text-sm text-muted py-2">暂无经验条目</p>}
          </Card>
        </div>

        {error && <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm mt-4">{error}</div>}

        <button
          onClick={start}
          className="mt-6 w-full bg-fg hover:opacity-90 text-canvas py-3 rounded-lg text-sm font-medium transition-colors"
        >
          开始评估（已勾选 {selected.size} 条参考材料）→
        </button>
      </div>
    )
  }

  // ---------------------------------------------------------------- 运行/完成阶段：分轴时间线
  const detailOf = (node: string) => result?.dimension_results?.[node]
  const statusOf = (node: string): NodeState => {
    const d = detailOf(node)
    if (d?.status) return d.status as NodeState
    return states[node] ?? 'waiting'
  }
  const subNodes = goalType === '要名' ? ['precedent'] : ['damages', 'recovery']

  const nodeCard = (
    node: string,
    extra?: ReactNode,
    opts?: { rerunnable?: boolean; skipIfBlocked?: boolean },
  ) => {
    const label = NODE_LABELS[node] ?? node
    const status = statusOf(node)
    const d = detailOf(node)
    const isStale = status === 'stale'
    const isSkipped = opts?.skipIfBlocked && blocked && !d && status === 'waiting'
    const rerunnable = opts?.rerunnable && RERUNNABLE.has(node)

    const right = (
      <>
        {isStale && <Badge variant="warning">待确认重跑</Badge>}
        {status === 'failed' && <Badge variant="danger">失败</Badge>}
        {status === 'blocked' && <Badge variant="danger">命中红线</Badge>}
        {rerunnable && rerunTarget !== node && (
          <button
            onClick={() => { setRerunTarget(node); setRerunGuidance('') }}
            disabled={rerunBusy}
            className="text-xs text-muted hover:text-fg border border-line rounded px-2 py-1 transition-colors"
          >重跑</button>
        )}
      </>
    )

    let body: ReactNode
    if (isSkipped) {
      body = <p className="text-sm text-muted">未执行（被前置红线拦截）</p>
    } else if (rerunTarget === node) {
      body = (
        <div className="mt-1">
          <textarea
            value={rerunGuidance}
            onChange={(e) => setRerunGuidance(e.target.value)}
            placeholder="可选：补充引导意见，将注入后续所有节点"
            rows={2}
            className="w-full text-sm border border-line rounded-lg p-2 bg-canvas focus:outline-none focus:border-fg"
          />
          <div className="flex gap-2 mt-2">
            <button
              onClick={() => doRerun(node)}
              disabled={rerunBusy}
              className="text-xs bg-fg hover:opacity-90 text-canvas rounded px-3 py-1.5 disabled:opacity-50"
            >{rerunBusy ? '重跑中…' : '确认重跑'}</button>
            <button
              onClick={() => { setRerunTarget(null); setRerunGuidance('') }}
              className="text-xs border border-line rounded px-3 py-1.5 hover:bg-surface"
            >取消</button>
          </div>
        </div>
      )
    } else if (status === 'running' && !d) {
      body = <p className="text-sm text-muted">计算中…</p>
    } else if (!d) {
      body = <p className="text-sm text-muted">待计算…</p>
    } else {
      body = extra
    }

    return <StepperNode status={status} label={label} right={right}>{body}</StepperNode>
  }

  // 单维度分数小卡（结论页/决策合成内复用）
  const ScoreCell = ({ label, score, t }: { label: string; score: number | null | undefined; t: Thresholds }) => (
    <div className="rounded-lg border border-line bg-canvas p-3">
      <div className="text-xs text-muted mb-1">{label}</div>
      <ScoreBadge score={score} t={t} />
    </div>
  )

  const renderDetail = (node: string): ReactNode => {
    const d = detailOf(node)
    if (!d) return null
    const r = d.result ?? {}
    const t = thresholds
    switch (node) {
      case 'evidence_review':
        return (
          <div className="text-sm text-muted">
            证据完整度 <b className="text-fg">{r.completeness ?? 0}%</b>
            · 缺口 <b className="text-fg">{r.gap_count ?? 0}</b> 项
            {r.note && <span className="text-muted"> · {r.note}</span>}
          </div>
        )
      case 'red_gate':
        if (r.blocked) {
          return <div className="verdict-banner tier-block">命中程序性红线，流程终止（具体红线见下方明细）</div>
        }
        return (
          <ul className="space-y-1.5">
            {(r.hits ?? []).map((h: any) => (
              <li key={h.rule_code} className="text-sm flex gap-2">
                <span className={`sev-${h.severity} font-medium shrink-0 w-10`}>{SEV_LABEL[h.severity] ?? h.severity}</span>
                <span className="text-muted"><b className="text-fg">{h.rule_name}</b>：{h.result}。{h.reason}</span>
              </li>
            ))}
          </ul>
        )
      case 'rights':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            {r.analysis && <p className="text-sm text-muted mt-2">{r.analysis}</p>}
            {(r.strengths?.length > 0 || r.risks?.length > 0) && (
              <div className="grid sm:grid-cols-2 gap-3 mt-2">
                <div>
                  <div className="text-xs text-muted mb-1">优势</div>
                  {(r.strengths ?? []).map((s: string, i: number) => (
                    <div key={i} className="text-sm text-[var(--success)]">+ {s}</div>
                  ))}
                </div>
                <div>
                  <div className="text-xs text-muted mb-1">风险</div>
                  {(r.risks ?? []).map((s: string, i: number) => (
                    <div key={i} className="text-sm text-[var(--danger)]">- {s}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      case 'infringement':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <div className="mt-2 space-y-1">
              {(r.elements ?? []).map((e: any, i: number) => (
                <div key={i} className="text-sm flex gap-2 items-start">
                  <span className={cn('shrink-0 px-1.5 rounded text-xs', elemColor(e.status))}>{e.status}</span>
                  <span className="text-muted"><b className="text-fg">{e.name}</b>：{e.analysis}</span>
                </div>
              ))}
            </div>
            {r.analysis && <p className="text-sm text-muted mt-1">{r.analysis}</p>}
          </div>
        )
      case 'procedure':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <div className="mt-2 space-y-1">
              {(r.risks ?? []).map((rk: any, i: number) => (
                <div key={i} className="text-sm flex gap-2 items-start">
                  <span className={cn('shrink-0 px-1.5 rounded text-xs', riskColor(rk.level))}>{RISK_LABEL[rk.level] ?? rk.level}</span>
                  <span className="text-muted"><b className="text-fg">{rk.item}</b>：{rk.detail}</span>
                </div>
              ))}
            </div>
            {r.analysis && <p className="text-sm text-muted mt-1">{r.analysis}</p>}
          </div>
        )
      case 'damages':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <div className="text-sm text-muted mt-1">
              判赔分布 P10/P50/P90：<b className="text-fg">{r.p10}</b> / <b className="text-fg">{r.p50}</b> / <b className="text-fg">{r.p90}</b> 万元
              · 回报倍数 <b className="text-fg">{r.return_multiple}</b>× · 侵权规模支撑 <b className="text-fg">{scaleLabel(r.scale_support)}</b>
            </div>
            {r.analysis && <p className="text-sm text-muted mt-1">{r.analysis}</p>}
          </div>
        )
      case 'recovery':
        return (
          <div>
            {r.recovery_ability != null ? (
              <ScoreBadge score={r.recovery_ability} t={t} />
            ) : (
              <span className="text-xs text-muted">未获取到被告企业画像，回款能力无法计算（业务预期将标注未完成）</span>
            )}
            {r.red_flags?.length > 0 && <div className="text-sm text-[var(--danger)] mt-1">回款风险：{r.red_flags.join('、')}</div>}
            {r.green_flags?.length > 0 && <div className="text-sm text-[var(--success)]">回款利好：{r.green_flags.join('、')}</div>}
          </div>
        )
      case 'precedent':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <div className="text-sm text-muted mt-1">
              首案指数 <b className="text-fg">{r.first_case_index}</b> · 影响层级 <b className="text-fg">{r.influence_level}</b>
            </div>
            {r.analysis && <p className="text-sm text-muted mt-1">{r.analysis}</p>}
          </div>
        )
      case 'synthesize': {
        const syn = d.result ?? {}
        return (
          <div>
            <div className="grid grid-cols-3 gap-3">
              <ScoreCell label="法律可行性" score={syn.scores?.legal_feasibility} t={t} />
              <ScoreCell label="业务预期" score={syn.scores?.business_expectation} t={t} />
              <ScoreCell label="主诉决策分" score={syn.scores?.final} t={t} />
            </div>
            <div className="text-sm text-muted mt-2">置信度 <b className="text-fg">{syn.confidence ?? '—'}%</b></div>
            {syn.missing?.length > 0 && (
              <div className="text-sm text-[var(--warning)] mt-1">未产出维度：{syn.missing.join('、')}</div>
            )}
            <ConclusionCard
              recommendation={syn.recommendation?.recommendation}
              reason={syn.recommendation?.reason}
              level={syn.recommendation?.level}
            />
          </div>
        )
      }
      default:
        return null
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-medium mb-1">评估分析</h1>
      <p className="text-sm text-muted mb-6">
        前置盘点 → 硬门禁 → 法律可行性（权利/侵权/程序）→ 业务预期 → 决策合成，
        每个环节均展示状态、分数（档位色）、结论与依据
      </p>
      {injectedInfo && (
        <div className="bg-[var(--info-soft)] text-[var(--info)] rounded-lg px-4 py-2 text-xs mb-4">{injectedInfo}</div>
      )}

      {AXES.map((group) => (
        <section key={group.axis} className="mb-6">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-muted tracking-wide">{group.axis}</span>
            <span className="flex-1 h-px bg-line" />
          </div>
          <div className="space-y-3">
            {group.nodes.map((node) => {
              if (node === 'business') {
                const b = detailOf('business')?.result ?? {}
                return (
                  <div key={node} className="space-y-3">
                    {nodeCard('business', (
                      <div className="text-sm text-muted">
                        目标：<b className="text-fg">{b.goal_type ?? goalType}</b>
                        {b.sub_dimensions?.length > 0 && (
                          <span> · 子维度 {b.sub_dimensions.map((s: string) => NODE_LABELS[s] ?? s).join(' + ')}</span>
                        )}
                      </div>
                    ), { rerunnable: true })}
                    {subNodes.map((sub) => (
                      <div key={sub} className="pl-5 border-l border-line">
                        {nodeCard(sub, renderDetail(sub), { skipIfBlocked: true })}
                      </div>
                    ))}
                  </div>
                )
              }
              return (
                <div key={node}>
                  {nodeCard(node, renderDetail(node), {
                    rerunnable: node !== 'red_gate' && node !== 'synthesize',
                    skipIfBlocked: true,
                  })}
                </div>
              )
            })}
          </div>
        </section>
      ))}

      {error && <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm mt-4">{error}</div>}

      {finished && (
        <button
          onClick={() => navigate(`/cases/${id}/dashboard`)}
          className="mt-6 w-full bg-fg hover:opacity-90 text-canvas py-3 rounded-lg text-sm font-medium transition-colors"
        >
          查看决策仪表盘 →
        </button>
      )}
    </div>
  )
}
