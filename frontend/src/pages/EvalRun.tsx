import { useState, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { STEP_MOTION } from '../lib/motion'
import { cn } from '../lib/utils'
import { type Thresholds, type NodeState } from '../lib/tiers'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { StepperNode } from '../components/ui/StepperNode'
import { ConclusionCard } from '../components/ui/ConclusionCard'

// 流程顺序与后端 NODE_ORDER 对齐（orchestrator.py）
const NODE_ORDER = [
  'evidence_review', 'red_gate', 'rights', 'infringement', 'procedure', 'business', 'synthesize',
]
const NODE_LABELS: Record<string, string> = {
  evidence_review: '证据盘点',
  red_gate: '红线检查',
  rights: '权利基础',
  infringement: '侵权认定',
  procedure: '诉讼程序',
  business: '业务预期',
  synthesize: '决策合成',
  damages: '判赔规模',
  recovery: '回款能力',
  precedent: '判例价值',
}
const RERUNNABLE = new Set(['evidence_review', 'rights', 'infringement', 'procedure', 'business'])

// 分轴呈现：让「每个环节的信息与结论」沿横轴分组清晰铺开。
// 导出供两处消费，避免「导航文字」和「分区标题」两边各写一份而走歪：
//   · 本文件：一次只渲染 EVAL_AXES 里的一个轴（单块逐步），切换动画走 lib/motion.ts 的 STEP_MOTION
//   · CaseWorkbench：渲染左侧章节导航（点击 = 切换当前轴，不再整页滚动）
// label 是导航用短名（去掉「轴」字，9rem 的窄列更清爽）；
// axis 是单步内分区标题，已与 label 统一去掉「轴」字。
export const EVAL_AXES: {
  id: string
  label: string
  axis: string
  nodes: string[]
  cols: string
}[] = [
  { id: 'eval-prep', label: '前置盘点', axis: '前置盘点', nodes: ['evidence_review', 'red_gate'], cols: 'sm:grid-cols-2' },
  { id: 'eval-legal', label: '法律可行性', axis: '法律可行性', nodes: ['rights', 'infringement', 'procedure'], cols: 'lg:grid-cols-3' },
  { id: 'eval-business', label: '业务预期', axis: '业务预期', nodes: ['business'], cols: '' },
  { id: 'eval-synth', label: '决策合成', axis: '决策合成', nodes: ['synthesize'], cols: '' },
]

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

/**
 * 评估详情（运行/完成时间线）。运行时状态由父级 CaseWorkbench 持有并下发，
 * 本组件只负责渲染；节点级重跑与「查看决策仪表盘」通过回调上抛。
 *
 * 呈现方式是「单块逐步」：一次只渲染 EVAL_AXES 里的一个轴，切换动画与案件详情共用
 * lib/motion.ts 的 STEP_MOTION。当前步由父级持有（activeAxis），因为左侧导航列在父级手上。
 */
export default function EvalRun({
  caseId,
  result,
  states,
  phase,
  finished,
  error,
  activeAxis,
  onAxisChange,
  onStart,
  onViewResult,
  onRerun,
}: {
  caseId: string
  result: any
  states: Record<string, NodeState>
  phase: 'prep' | 'running' | 'done'
  finished: string
  error: string
  /** 当前展示的轴（EVAL_AXES 的 id） */
  activeAxis: string
  /** 切换轴：左侧导航列与窄屏分段控件都走它 */
  onAxisChange: (id: string) => void
  onStart?: () => void
  onViewResult: () => void
  onRerun: (node: string, guidance: string) => Promise<void>
}) {
  const [rerunTarget, setRerunTarget] = useState<string | null>(null)
  const [rerunGuidance, setRerunGuidance] = useState('')
  const [rerunBusy, setRerunBusy] = useState(false)

  const thresholds: Thresholds =
    result?.thresholds ?? { go: 78, patch: 62, quadrant_mid: 78, power_mean_p: -0.5 }
  const goalType: string = result?.goal_type ?? '要钱'
  const blocked = Boolean(result?.dimension_results?.red_gate?.result?.blocked)

  const doRerun = async (node: string) => {
    setRerunBusy(true)
    try {
      await onRerun(node, rerunGuidance)
    } finally {
      setRerunBusy(false)
      setRerunTarget(null)
      setRerunGuidance('')
    }
  }

  // 尚未开始评估（本会话未运行且后端也无历史结果）
  if (!result && phase === 'prep') {
    return (
      <div className="bg-surface border border-line rounded-xl p-8 text-center">
        <p className="text-muted text-sm mb-4">
          本案尚未开始评估。参考材料将在启动后由系统在后台自动召回并注入评估节点。
        </p>
        {onStart && (
          <button
            onClick={onStart}
            className="bg-fg hover:opacity-90 text-canvas px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
          >
            开始评估 →
          </button>
        )}
      </div>
    )
  }

  const detailOf = (node: string) => result?.dimension_results?.[node]
  const statusOf = (node: string): NodeState => {
    const d = detailOf(node)
    if (d?.status) return d.status as NodeState
    return states[node] ?? 'waiting'
  }
  const subNodes = goalType === '要名' ? ['precedent'] : ['damages', 'recovery']

  // 当前展示的轴（单块逐步）：父级持有一个 activeAxis，这里只渲染命中的那一个。
  const activeGroup = EVAL_AXES.find((a) => a.id === activeAxis) ?? EVAL_AXES[0]

  // 轴标题旁的维度 chip 点击：平滑滚动定位到对应节点卡（node-* id 由下方节点卡 wrapper 提供）
  const scrollToNode = (node: string) => {
    document.getElementById('node-' + node)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

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
          // 命中红线时除横幅外，必须把「具体红线」逐条列清（含 result + reason），
          // 否则用户只看到"流程终止"却不知为何——尤其要把"已上传但系统未能读取"
          // 这类系统侧原因与真实证据缺失区分开。
          const blockers = (r.hits ?? []).filter((h: any) => h.severity === 'block')
          const unread = (r.hits ?? []).filter(
            (h: any) => h.severity === 'warning' && /已上传但系统未能读取/.test(h.result || ''),
          )
          return (
            <div className="space-y-2">
              <div className="verdict-banner tier-block">命中程序性红线，流程终止</div>
              <ul className="space-y-1.5">
                {blockers.map((h: any) => (
                  <li key={h.rule_code} className="text-sm flex gap-2">
                    <span className={`sev-${h.severity} font-medium shrink-0 w-10`}>{SEV_LABEL[h.severity] ?? h.severity}</span>
                    <span className="text-muted"><b className="text-fg">{h.rule_name}</b>：{h.result}。{h.reason}</span>
                  </li>
                ))}
              </ul>
              {unread.length > 0 && (
                <div className="rounded-md border border-line bg-canvas p-3">
                  <div className="text-xs text-[var(--warning)] font-medium mb-1">
                    另：以下证据已上传但系统未能读取（属识别局限，非证据缺失）
                  </div>
                  <ul className="space-y-1">
                    {unread.map((h: any) => (
                      <li key={h.rule_code} className="text-xs text-muted">
                        · {h.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )
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
    <>
      {/* 窄屏兜底：左侧导航列在 xl 以下隐藏，用横向分段控件补上切换入口，否则其余轴无法访问 */}
      <div className="flex flex-wrap gap-2 mb-4 xl:hidden">
        {EVAL_AXES.map((s) => {
          const on = activeAxis === s.id
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onAxisChange(s.id)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-sm border transition-colors',
                on
                  ? 'bg-fg text-canvas border-fg font-medium'
                  : 'bg-surface text-muted border-line hover:text-fg',
              )}
            >
              {s.label}
            </button>
          )
        })}
      </div>

      {error && (
        <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm mb-4">{error}</div>
      )}

      {/* 单块逐步：一次只渲染 activeGroup，切换走 STEP_MOTION（与案件详情一致）。
          mode="wait" 保证旧块完全退场后再进新块，避免两块共存导致高度抖动。 */}
      <AnimatePresence mode="wait">
        <motion.div key={activeGroup.id} {...STEP_MOTION}>
          <section>
            <div className="flex items-center gap-2 mb-3 flex-wrap">
              <span className="text-xs font-medium text-muted tracking-wide">{activeGroup.axis}</span>
              {activeGroup.nodes.length > 1 && (
                <div className="flex flex-wrap gap-1.5">
                  {activeGroup.nodes.map((node) => (
                    <button
                      key={node}
                      type="button"
                      onClick={() => scrollToNode(node)}
                      className="text-xs px-2 py-0.5 rounded-full border border-line text-muted hover:text-fg hover:border-fg transition-colors"
                    >
                      {NODE_LABELS[node] ?? node}
                    </button>
                  ))}
                </div>
              )}
              <span className="flex-1 h-px bg-line" />
            </div>
            <div className={cn('grid gap-3', activeGroup.cols)}>
              {activeGroup.nodes.map((node) => {
                if (node === 'business') {
                  const b = detailOf('business')?.result ?? {}
                  return (
                    <div key={node} id={'node-' + node} className="space-y-3 scroll-mt-24">
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
                  <div key={node} id={'node-' + node} className="scroll-mt-24">
                    {nodeCard(node, renderDetail(node), {
                      rerunnable: node !== 'red_gate' && node !== 'synthesize',
                      skipIfBlocked: true,
                    })}
                  </div>
                )
              })}
            </div>
          </section>
        </motion.div>
      </AnimatePresence>

      {result?.scores?.final != null && (
        <button
          onClick={onViewResult}
          className="mt-6 w-full bg-fg hover:opacity-90 text-canvas py-3 rounded-lg text-sm font-medium transition-colors"
        >
          查看决策仪表盘 →
        </button>
      )}
    </>
  )
}
