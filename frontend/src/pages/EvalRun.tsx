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
import { EvalTrace } from '../components/EvalTrace'
import type { EvalEvent } from '../api'

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
const RERUNNABLE = new Set(['evidence_review', 'red_gate', 'rights', 'infringement', 'procedure', 'business'])
// 判赔规模/回款能力/判例价值 是 business 节点的子维度、不在 NODE_ORDER 里，无法单独重跑；
// 它们的「重跑」= 重跑父节点 business（两个子维度一起刷新）。
const RERUN_AS: Record<string, string> = { damages: 'business', recovery: 'business', precedent: 'business' }
const rerunNodeOf = (node: string) => RERUN_AS[node] ?? node

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
  // 模拟法庭不在自动流程里（手动 opt-in 的压力测试），nodes 空 = 无 chip，选中时走专属面板分支
  { id: 'eval-moot', label: '模拟法庭（可选）', axis: '模拟法庭', nodes: [], cols: '' },
]

const SEV_LABEL: Record<string, string> = { pass: '通过', warning: '警示', block: '拦截' }
const RISK_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低', none: '无' }

// 企查查各阶段的界面名。后端 key 形如 "A_主体锁定"、"E_人员风险扫描"，
// 带字母前缀加下划线直接摊在界面上没法读，统一换成一句能看懂的话。
const QCC_STAGE_LABELS: Record<string, string> = {
  'A_主体锁定': '锁定被告主体',
  'B_基本盘': '工商登记与财务',
  'C_风险分诊': '风险整体分诊',
  'D_风险下钻': '司法与经营风险明细',
  'E_人员风险': '实控人与高管风险',
  'E_人员风险扫描': '实控人与高管风险',
  'E_失信被执行限高': '实控人失信与限高',
  'E_资产状况': '实控人资产状况',
  'F_经营规模': '商标与经营渠道',
  'G_诉讼时间': '被诉历史与周期',
}
const qccStageLabel = (k: string) => QCC_STAGE_LABELS[k] ?? k.replace(/^[A-Z]_/, '')

function elemColor(s?: string) {
  if (s === '满足') return 'bg-[var(--success-soft)] text-[var(--success)]'
  if (s === '存疑') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  if (s === '不满足') return 'bg-[var(--danger-soft)] text-[var(--danger)]'
  return 'bg-surface text-muted'
}

// 证据要件的三种状态（sufficient/partial/missing）：界面上必须说人话，
// 「partial」要写成「不足」而不是「部分满足」，否则用户读不出该不该补。
const EV_STATUS_LABEL: Record<string, string> = {
  sufficient: '齐备',
  partial: '不足',
  missing: '缺失',
}
function evStatusColor(s?: string) {
  if (s === 'sufficient') return 'bg-[var(--success-soft)] text-[var(--success)]'
  if (s === 'partial') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  return 'bg-[var(--danger-soft)] text-[var(--danger)]'
}

/** 分数格式化：整数不显示小数尾巴（78.0 → 78） */
function fmtNum(v: any): string {
  if (v == null || v === '') return '—'
  const f = Number(v)
  if (Number.isNaN(f)) return String(v)
  return Number.isInteger(f) ? String(f) : f.toFixed(1)
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
  onStartMoot,
  traceEvents,
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
  /** 启动模拟法庭压力测试：跳转 /cases/{id}/moot 内嵌模式（SSE 逐轮直播在那一页） */
  onStartMoot: () => void
  /** 评估过程事件流（node_step / mcp_call / 节点起止），由父级持有，避免切标签时丢失 */
  traceEvents: EvalEvent[]
}) {
  const [rerunTarget, setRerunTarget] = useState<string | null>(null)
  const [rerunGuidance, setRerunGuidance] = useState('')
  const [rerunBusy, setRerunBusy] = useState(false)

  const thresholds: Thresholds =
    result?.thresholds ?? { go: 78, patch: 62, quadrant_mid: 78, power_mean_p: -0.5 }
  const goalType: string = result?.goal_type ?? '要钱'
  const blocked = Boolean(result?.dimension_results?.red_gate?.result?.blocked)
  // 模拟法庭状态（与决策仪表盘同口径：correction_coeff 存在且 ≠1 视为已回写）
  const mootCoeff = result?.correction_coeff
  const mootDone = mootCoeff != null && mootCoeff !== 1

  const doRerun = async (node: string) => {
    setRerunBusy(true)
    try {
      await onRerun(rerunNodeOf(node), rerunGuidance)
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

  // 轴内维度切换：多节点轴一次只显示一个维度。activeNode 记忆轴内选中；
  // 选中节点不属于当前轴（刚切轴）时回退到该轴第一个节点。
  const [activeNode, setActiveNode] = useState<string | null>(null)
  // 业务预期轴的可切换小标题是子维度（判赔规模/回款能力；要名为判例价值）；其他轴用 activeGroup.nodes
  const chipNodes = activeGroup.id === 'eval-business' ? subNodes : activeGroup.nodes
  const currentNode = chipNodes.includes(activeNode ?? '') ? (activeNode as string) : chipNodes[0]

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
    const rerunnable = opts?.rerunnable && RERUNNABLE.has(rerunNodeOf(node))

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
      // 运行中不再只显示「计算中…」——把这一环节已经走过的步骤实时列出来
      const steps = traceEvents.filter(
        (e) => (e.event === 'node_step' || e.event === 'mcp_call') && e.node === node,
      )
      body = steps.length > 0 ? (
        <ul className="space-y-1.5">
          {steps.map((s, i) => (
            <li key={i} className="text-sm text-muted">
              <div className="flex gap-2 items-start">
                {s.event === 'mcp_call' ? (
                  <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded border border-line text-[11px]">
                    {s.vendor ?? '外部数据'}
                  </span>
                ) : (
                  <span className="shrink-0 mt-[7px] w-1.5 h-1.5 rounded-full bg-line" />
                )}
                <span className={cn(s.event === 'mcp_call' && 'text-fg')}>{s.text}</span>
              </div>
              {s.detail && (
                <p className="text-xs text-muted mt-0.5 ml-[14px] whitespace-pre-line">{s.detail}</p>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">正在处理这一步，稍等片刻…</p>
      )
    } else if (!d) {
      body = <p className="text-sm text-muted">等前面的环节完成后会自动开始</p>
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

  // 判断依据通用区块：标题 + 一段解释。用于回答「这个结论是从哪来的」，
  // 四类来源固定为「证据依据 / 规则与算法依据 / 外部数据依据 / 模型判断」。
  const Basis = ({ title, children }: { title: string; children: ReactNode }) => (
    <div className="border-l-2 border-line pl-3">
      <div className="text-xs font-medium text-fg mb-0.5">{title}</div>
      <div className="text-sm text-muted leading-relaxed">{children}</div>
    </div>
  )

  const BasisList = ({ children }: { children: ReactNode }) => (
    <div className="mt-3 pt-3 border-t border-line space-y-3">
      <div className="text-xs text-muted">判断依据</div>
      {children}
    </div>
  )

  const ev = result?.evidence ?? {}
  const matrix: any[] = ev.matrix ?? []
  const gaps: any[] = ev.gap_list ?? []
  const recalled: any[] = result?.recalled_materials ?? []
  const qccMetrics: any = result?.defendant_profile?.metrics ?? {}
  const qccStages: Record<string, any> = result?.defendant_profile?.stages ?? {}
  const goalIsMoney = goalType === '要钱'

  // 「结论从哪来」的最硬一环：列出本维度实际依赖的证据要件条目（含法律出处与核对结论）。
  // 不写「参考了 N 项要件」这种笼统话——用户要能逐条对上自己交了什么、缺什么。
  const EvItems = ({ categories }: { categories: string[] }) => {
    const sub = matrix.filter((m: any) => categories.includes(m.category))
    if (sub.length === 0) {
      return <>本维度没有直接绑定的证据要件，判断主要依据案情描述与自动召回的相关材料。</>
    }
    const ok = sub.filter((m: any) => m.status === 'sufficient').length
    return (
      <>
        本维度的判断主要落在「{categories.join('、')}」这一类上，共 {sub.length} 项要件，
        其中 {ok} 项齐备{sub.length - ok > 0 ? `、${sub.length - ok} 项不足或缺失` : ''}。逐条如下：
        <ul className="mt-1.5 space-y-1">
          {sub.map((m: any) => (
            <li key={m.id} className="flex gap-2 items-start">
              <span className={cn('shrink-0 px-1.5 rounded text-xs', evStatusColor(m.status))}>
                {EV_STATUS_LABEL[m.status] ?? m.status}
              </span>
              <span>
                <b className="text-fg">{m.item}</b>
                <span className="text-xs">（依据 {m.basis}）</span>：{m.reason}
              </span>
            </li>
          ))}
        </ul>
      </>
    )
  }

  // 三个法律维度的依据区块：证据要件 → 评分口径 → 参考材料。
  // categories 传该维度真正依赖的证据类别，避免三个维度写同一段套话。
  // 刻意不在这里列企查查数据：它只影响业务预期，放进「判断依据」会让人误以为它改变了法律判断。
  const legalBasis = (label: string, score: any, categories: string[] = []) => {
    const corr = result?.correction_coeff
    return (
      <BasisList>
        <Basis title="证据依据">
          <EvItems categories={categories} />
        </Basis>
        <Basis title="评分依据">
          {label}得分 {fmtNum(score)} 分。它与权利基础、侵权认定、诉讼程序三个维度的得分一起，
          经幂平均（p = {thresholds.power_mean_p ?? -0.5}，对低分更敏感）合成「法律可行性」
          {corr != null && corr !== 1
            ? `，再乘上模拟法庭给出的修正系数 ${corr}。`
            : '；目前还没跑模拟法庭，因此不乘修正系数（按 1.0 计入）。'}
          用幂平均而不是普通算术平均的原因是：三个维度里只要有一个明显偏低，整体就会被拉下来，
          不会被另外两个高分「平均」掉——这与法官对「任一要件不成立即败诉」的直觉一致。
        </Basis>
        {recalled.length > 0 && (
          <Basis title="参考材料">
            系统按案由与案情自动召回了 {recalled.length} 条相关材料并注入到本维度，包括
            {recalled.slice(0, 3).map((m: any) => m.title).join('、')}
            {recalled.length > 3 ? ` 等` : ''}。它们的作用是给模型提供同类案件的裁判尺度，属于辅助参考，
            不替代对本案证据的核对。
          </Basis>
        )}
      </BasisList>
    )
  }

  const renderDetail = (node: string): ReactNode => {
    const d = detailOf(node)
    if (!d) return null
    const r = d.result ?? {}
    const t = thresholds
    switch (node) {
      case 'evidence_review': {
        const sufficient = matrix.filter((m: any) => m.status === 'sufficient')
        const lacking = matrix.length - sufficient.length
        return (
          <div>
            <p className="text-sm text-muted leading-relaxed">
              已逐项核对 {matrix.length} 项要件，其中 {sufficient.length} 项材料齐备
              {lacking > 0 ? `、${lacking} 项仍有欠缺` : ''}，证据完整度 {r.completeness ?? 0}%。
              {r.note ? ` ${r.note}` : ''}
            </p>
            {matrix.length > 0 && (
              <div className="mt-3 space-y-1.5">
                {matrix.map((m: any) => (
                  <div key={m.id} className="text-sm flex gap-2 items-start">
                    <span className={cn('shrink-0 px-1.5 rounded text-xs', evStatusColor(m.status))}>
                      {EV_STATUS_LABEL[m.status] ?? m.status}
                    </span>
                    <span className="text-muted">
                      <b className="text-fg">{m.item}</b>
                      <span className="text-xs">（{m.category} · 支撑要件：{m.element}）</span>：{m.reason}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <BasisList>
              <Basis title="证据依据">
                每项要件都带法律出处（如「{matrix[0]?.item ?? '—'}」对应 {matrix[0]?.basis ?? '—'}），
                系统据此逐项判定齐备 / 不足 / 缺失，再按 齐备 1 分、不足 0.5 分、缺失 0 分算出完整度
                {r.completeness ?? 0}%。这份清单同时决定红线检查中「权利证明」「侵权证据」「损失证据」
                三条规则能否放行。
              </Basis>
              {recalled.length > 0 && (
                <Basis title="参考材料">
                  系统按案由与案情自动检索到 {recalled.length} 条相关材料，已注入后续所有判断环节：
                  {recalled.slice(0, 3).map((m: any) => m.title).join('、')}
                  {recalled.length > 3 ? ` 等 ${recalled.length} 条` : ''}。
                </Basis>
              )}
              {gaps.length > 0 && (
                <Basis title="缺口与建议">
                  {gaps.slice(0, 5).map((g: any, i: number) => (
                    <div key={i}>· {g.suggestion || `补充${g.item}`}</div>
                  ))}
                  {gaps.length > 5 && <div className="text-xs">（其余 {gaps.length - 5} 项见报告缺口清单）</div>}
                </Basis>
              )}
            </BasisList>
          </div>
        )
      }
      case 'red_gate': {
        // 红线检查的依据要说清三件事：规则从哪来、每条看了什么、为什么某些能拦停流程。
        const hits: any[] = r.hits ?? []
        const nPass = hits.filter((h: any) => h.severity === 'pass').length
        const nWarn = hits.filter((h: any) => h.severity === 'warning').length
        const nBlock = hits.filter((h: any) => h.severity === 'block').length
        // 这三条规则不是独立判断的，它们的输入正是证据盘点的结果
        const evidenceFed = hits.filter((h: any) =>
          ['权利证明', '侵权证据', '损失证据'].some((k) => (h.rule_name ?? '').includes(k)),
        )
        const gateBasis = (
          <BasisList>
            <Basis title="规则依据">
              这一步不看模型输出，而是逐条套用 {hits.length} 条程序性规则（诉讼时效、主体资格、仲裁条款、
              权利证明、侵权证据、损失证据）。本次结果为 {nPass} 条通过、{nWarn} 条警示、{nBlock} 条拦截。
              规则的判定标准写死在引擎里，所以同一份材料重跑结果稳定、不会因为换模型而漂移。
            </Basis>
            <Basis title="证据依据">
              {evidenceFed.length > 0
                ? `其中「${evidenceFed.map((h: any) => h.rule_name).join('」「')}」这几条直接读取证据盘点的核对结果（当前完整度 ${r.completeness ?? ev.completeness ?? 0}%），材料齐备即放行、缺失即拦截。`
                : `这一步不单独读材料，全部输入来自证据盘点与案情描述（当前证据完整度 ${ev.completeness ?? 0}%）。`}
            </Basis>
            <Basis title="结果含义">
              通过 = 不构成障碍，直接进入后续判断；警示 = 可以继续推进，但建议补强相关材料；
              拦截 = 命中硬性障碍，评估会就此终止——因为后面的权利基础、侵权认定即使算出来也没有落地意义。
              {r.blocked
                ? '本次已命中拦截，因此后续环节未执行。'
                : '本次没有命中拦截，因此后续环节正常执行。'}
            </Basis>
          </BasisList>
        )
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
              {gateBasis}
            </div>
          )
        }
        return (
          <div>
            <p className="text-sm text-muted leading-relaxed">
              六条程序性规则逐条走完后没有出现硬性障碍，评估可以继续。
              {nWarn > 0
                ? `其中 ${nWarn} 条给出警示，不影响推进，但建议在起诉前把对应材料补齐。`
                : '六条全部通过，没有需要补强的地方。'}
            </p>
            <ul className="space-y-1.5 mt-2">
              {(r.hits ?? []).map((h: any) => (
                <li key={h.rule_code} className="text-sm flex gap-2">
                  <span className={`sev-${h.severity} font-medium shrink-0 w-10`}>{SEV_LABEL[h.severity] ?? h.severity}</span>
                  <span className="text-muted"><b className="text-fg">{h.rule_name}</b>：{h.result}。{h.reason}</span>
                </li>
              ))}
            </ul>
            {gateBasis}
          </div>
        )
      }
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
            {legalBasis('权利基础', r.score, ['权利基础证据'])}
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
            {legalBasis('侵权认定', r.score, ['侵权认定证据', '取证技术规范'])}
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
            {legalBasis('诉讼程序', r.score, ['取证技术规范'])}
          </div>
        )
      case 'damages':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <p className="text-sm text-muted mt-2 leading-relaxed">
              按同类案件的判赔水平推算，本案最可能拿到 <b className="text-fg">{fmtNum(r.p50)} 万元</b> 左右
              （偏保守的情形约 {fmtNum(r.p10)} 万元，顺利的情形可达 {fmtNum(r.p90)} 万元）。
              相对预估的维权投入，大致能收回 <b className="text-fg">{fmtNum(r.return_multiple)} 倍</b>。
              案情里交代的侵权规模对高判赔的支撑度为「{scaleLabel(r.scale_support)}」
              {r.scale_support === 'low' ? '——规模证据偏弱，判赔可能贴着下限走。' : '。'}
            </p>
            <BasisList>
              <Basis title="证据依据">
                <EvItems categories={['损害赔偿证据']} />
              </Basis>
              <Basis title="规则与算法依据">
                判赔区间不是拍脑袋给的：以本案由的<b>法定赔偿区间</b>为边界、以<b>类案判赔水平</b>为锚，
                再结合案情里的侵权规模推出 P10 / P50 / P90 三个分位点。
                「回报倍数」= P50 判赔额 ÷ 预估总成本（律师费、诉讼费、公证取证费等，按 8–15 万估）。
                这一维度得 {fmtNum(r.score)} 分，会与回款能力一起经幂平均合成「业务预期」。
                这两者是短板效应：判得再多，收不回来也白搭；能收回来但判得太少，同样不划算。
              </Basis>
              {(qccMetrics.damages_adjustment || (qccMetrics.time_extra_months ?? 0) > 0) && (
                <Basis title="外部数据依据">
                  被告的经营规模来自企查查查询，据此给出判赔调整「{qccMetrics.damages_adjustment ?? '基准'}」——
                  线上店铺、APP、小程序、公众号、抖音号等销售渠道越多，说明侵权铺得越广、可主张的判赔越高
                  （渠道 ≥5 个上调，商标资产 ≥20 件按成熟品牌上调，几乎无渠道则下调）。
                  {(qccMetrics.time_extra_months ?? 0) > 0 && (
                    <>同时预计诉讼周期会比常规多约 {qccMetrics.time_extra_months} 个月，
                      这个延长量由被告的被诉历史件数、终本案件数与被执行次数累加而来。</>
                  )}
                </Basis>
              )}
              {r.analysis && <Basis title="模型判断">{r.analysis}</Basis>}
            </BasisList>
          </div>
        )
      case 'recovery': {
        const stageKeys = Object.keys(qccStages)
        return (
          <div>
            {r.recovery_ability != null ? (
              <ScoreBadge score={r.recovery_ability} t={t} />
            ) : (
              <span className="text-xs text-muted">未获取到被告企业画像，回款能力无法计算（业务预期将标注未完成）</span>
            )}
            <p className="text-sm text-muted mt-2 leading-relaxed">
              {r.recovery_ability != null
                ? `赢了官司不等于拿得到钱。系统按被告的工商状态与涉诉记录判断，胜诉后实际能够收回款项的可能性约为 ${fmtNum(r.recovery_ability)}%。`
                : '缺少被告的企业画像，这一步无法给出回款结论，业务预期会因此标记为未完成。'}
              {r.green_flags?.length > 0 && ` 有利的一面是${r.green_flags.join('、')}。`}
              {r.red_flags?.length > 0 && ` 需要警惕的是${r.red_flags.join('、')}。`}
            </p>
            <BasisList>
              <Basis title="外部数据依据">
                回款能力的原始数据全部来自企查查的被告画像，共 8 个环节
                {stageKeys.length > 0
                  ? `，本次实际取到 ${stageKeys.length} 个环节的结果：${stageKeys.map(qccStageLabel).join('、')}。`
                  : '（本次未取到阶段明细）。'}
                查询口径是先按名称模糊搜索出候选主体，再按行业与所在地消歧后锁定被告本体，避免查错公司。
                这一步全程不调用大模型，所以不消耗模型额度。
              </Basis>
              <Basis title="规则与算法依据">
                分数来自一张写死的规则表，同样材料重跑结果稳定：
                基准 50% 起算，再逐项按倍数调整——失信被执行 ×0.1、被执行 3 次以上 ×0.3、终本案件 3 件以上 ×0.4、
                严重违法 ×0.7、经营异常 ×0.8、实控人失信 ×0.6；上市公司 ×1.3、有公开财务数据 ×1.2、
                实控人有可追溯资产 ×1.15。注销、清算、破产重整任一出现则直接归零——主体都不存在了，判决无从执行。
                连乘后截断到 0–100%，得到 {fmtNum(r.recovery_ability)}%。
              </Basis>
              <Basis title="风险与利好信号">
                {r.red_flags?.length > 0 || r.green_flags?.length > 0 ? (
                  <>
                    {r.red_flags?.length > 0 && <div>风险信号（{r.red_flags.length} 项）：{r.red_flags.join('、')}</div>}
                    {r.green_flags?.length > 0 && <div>利好信号（{r.green_flags.length} 项）：{r.green_flags.join('、')}</div>}
                    这些信号直接对应上面的规则表：每命中一项就乘一次对应倍数，因此信号越多、分数偏离基准 50% 越远。
                  </>
                ) : (
                  '本次没有命中任何风险或利好信号，因此分数停留在基准 50% 附近——既没有明显收款障碍，也没有额外加分项。'
                )}
              </Basis>
            </BasisList>
          </div>
        )
      }
      case 'precedent':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <p className="text-sm text-muted mt-2 leading-relaxed">
              判例价值关注的是「打赢之后还能带来什么」。本案首案指数 {fmtNum(r.first_case_index)}，
              影响层级为「{r.influence_level ?? '—'}」
              {r.influence_level === '行业级'
                ? '——胜诉的影响会溢出到整个行业，适合作为对外宣传与行业博弈的筹码。'
                : '——影响主要限于本案或本区域。'}
            </p>
            <BasisList>
              <Basis title="证据依据">
                <EvItems categories={['权利基础证据', '侵权认定证据']} />
              </Basis>
              <Basis title="规则与算法依据">
                选「要名」时，业务预期直接取判例价值本身（不像「要钱」那样把判赔规模与回款能力聚合起来），
                因为名誉导向的目标看的是规则影响力而不是钱。
                判例价值综合四方面：是否不存在同类在先判例（首案潜力）、是否够得上最高法指导性案例与典型案例的遴选标准、
                胜诉对同行其他侵权者的震慑力、能否推动模糊的法律规则变得明确。
              </Basis>
              {r.analysis && <Basis title="模型判断">{r.analysis}</Basis>}
            </BasisList>
          </div>
        )
      case 'synthesize': {
        const syn = d.result ?? {}
        const dimScore = (n: string) => result?.dimension_results?.[n]?.result?.score
        const legalParts: [string, any][] = [
          ['权利基础', dimScore('rights')],
          ['侵权认定', dimScore('infringement')],
          ['诉讼程序', dimScore('procedure')],
        ]
        const bizParts: [string, any][] =
          goalIsMoney
            ? [['判赔规模', dimScore('damages')], ['回款能力', dimScore('recovery')]]
            : [['判例价值', dimScore('precedent')]]
        return (
          <div>
            <p className="text-sm text-muted leading-relaxed">
              把前面各个环节的结论汇总成一句话：法律上站不站得住、经济上划不划算，两者共同决定要不要起诉。
            </p>
            <div className="grid grid-cols-3 gap-3 mt-3">
              <ScoreCell label="法律可行性" score={syn.scores?.legal_feasibility} t={t} />
              <ScoreCell label="业务预期" score={syn.scores?.business_expectation} t={t} />
              <ScoreCell label="主诉决策分" score={syn.scores?.final} t={t} />
            </div>
            <div className="text-sm text-muted mt-2">
              置信度 <b className="text-fg">{fmtNum(syn.confidence)}%</b>
              <span className="text-xs">
                （由证据完整度决定，与得分分开计算——证据越全，这个结论越值得信）
              </span>
            </div>
            {syn.missing?.length > 0 && (
              <div className="text-sm text-[var(--warning)] mt-1">
                未产出维度：{syn.missing.join('、')}。这些维度没有拿到分数，因此不会参与合成，
                结论的完整度会因此打折。
              </div>
            )}
            {/* 方案A：模拟法庭状态行 —— 未进行给入口（跳内嵌模式），已回写显示系数 */}
            <div className="text-sm text-muted mt-2 flex items-center gap-2 flex-wrap">
              {mootDone ? (
                <span>模拟法庭已回写 · 修正系数 <b className="text-fg">{mootCoeff}</b></span>
              ) : (
                <span>模拟法庭未进行 · 修正系数 1.0（法律可行性暂未修正）</span>
              )}
              {!mootDone && result?.scores?.final != null && (
                <button
                  type="button"
                  onClick={onStartMoot}
                  className="text-xs font-medium text-muted hover:text-fg transition-colors"
                >
                  启动压力测试 →
                </button>
              )}
            </div>
            <ConclusionCard
              recommendation={syn.recommendation?.recommendation}
              reason={syn.recommendation?.reason}
              level={syn.recommendation?.level}
            />
            <BasisList>
              <Basis title="评分依据">
                这一步不重新判断案情，只是把前面已经算出的分数按固定公式合起来。先算「法律可行性」：
                {legalParts.map(([label, s], i) => (
                  <span key={label}>{i > 0 ? '、' : ''}{label} {fmtNum(s)} 分</span>
                ))}
                三个维度经<b>幂平均</b>（p = {t.power_mean_p ?? -0.5}，对低分更敏感）合成
                {mootDone
                  ? `，再乘上模拟法庭给出的修正系数 ${mootCoeff}`
                  : '（尚未跑模拟法庭，按系数 1.0 计入）'}
                ，得到 {fmtNum(syn.scores?.legal_feasibility)} 分。
                <br />
                再算「业务预期」：
                {goalIsMoney
                  ? <>判赔规模 {fmtNum(dimScore('damages'))} 分与回款能力 {fmtNum(dimScore('recovery'))} 分同样经幂平均合成</>
                  : <>「要名」目标下直接取判例价值 {fmtNum(dimScore('precedent'))} 分</>}
                ，得到 {fmtNum(syn.scores?.business_expectation)} 分。
                <br />
                最后「主诉决策分」= 法律可行性与业务预期的幂平均 = {fmtNum(syn.scores?.final)} 分。
                用幂平均而非算术平均是为了体现短板效应：法律上站得住但收不回钱，或反过来，都不足以支撑起诉决策。
              </Basis>
              <Basis title="证据依据">
                本次证据完整度为 {ev.completeness ?? 0}%，
                {gaps.length > 0
                  ? `还有 ${gaps.length} 项缺口未补齐。补齐后不仅各维度得分可能上升，置信度也会同步提高。`
                  : '要件齐备，没有发现明显缺口。'}
                {recalled.length > 0 && ` 另外，评估全程自动召回了 ${recalled.length} 条相关材料作为辅助参考。`}
              </Basis>
              <Basis title="结论口径">
                结论按固定档位给出，不以分数线性外推：
                主诉决策分 ≥ {t.go} 分建议优先启动；≥ {t.patch} 分建议补齐短板后再启动；
                低于 {t.patch} 分则暂缓。
                {blocked && ' 若存在程序性红线，无论分数高低都先解决红线问题——这条规则优先于评分。'}
              </Basis>
            </BasisList>
          </div>
        )
      }
      default:
        return null
    }
  }

  return (
    <>
      {/* 评估过程时间线：进行中实时追加、跑完可折叠回看。
          只在「本次会话真的捕获到事件」时占位——刷新页面后事件流为空，
          此时摊一个「没有捕获到过程记录」的空盒子只会白占地方、还显得像出错。 */}
      {traceEvents.length > 0 && (
        <div className="mb-4">
          <EvalTrace events={traceEvents} states={states} phase={phase} />
        </div>
      )}
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
              {chipNodes.length > 1 && (
                <div className="flex flex-wrap gap-1.5">
                  {chipNodes.map((node) => {
                    const on = node === currentNode
                    return (
                      <button
                        key={node}
                        type="button"
                        onClick={() => setActiveNode(node)}
                        className={cn(
                          'text-xs px-2.5 py-1 rounded-full border transition-colors',
                          on
                            ? 'bg-fg text-canvas border-fg font-medium'
                            : 'bg-surface text-muted border-line hover:text-fg hover:border-fg',
                        )}
                      >
                        {NODE_LABELS[node] ?? node}
                      </button>
                    )
                  })}
                </div>
              )}
              <span className="flex-1 h-px bg-line" />
              {result?.scores?.final != null && (
                <button
                  type="button"
                  onClick={onViewResult}
                  className="shrink-0 text-xs font-medium text-muted hover:text-fg transition-colors"
                >
                  查看决策仪表盘 →
                </button>
              )}
            </div>
            {activeGroup.id === 'eval-moot' ? (
              // 模拟法庭（可选）：不在自动流程里的压力测试入口，选中此轴时渲染专属面板
              <div className="rounded-xl border border-line bg-canvas p-5 space-y-4">
                <div className="flex items-center gap-2 text-sm">
                  <span
                    className={cn(
                      'w-2 h-2 rounded-full shrink-0',
                      mootDone ? 'bg-[var(--success)]' : 'bg-[var(--warning)]',
                    )}
                  />
                  {mootDone ? (
                    <span className="text-fg">
                      已回写 · 修正系数 <b>{mootCoeff}</b>（法律可行性已按模拟法庭结论修正）
                    </span>
                  ) : (
                    <span className="text-fg">未进行 · 修正系数 1.0（法律可行性暂未修正）</span>
                  )}
                </div>
                <p className="text-sm text-muted">
                  评估完成后的对抗压力测试：五步庭审对抗 → 法官归纳修正系数 → 回写并重算决策合成。未进行时法律可行性按系数 1.0 计算。
                </p>
                {result?.scores?.final != null ? (
                  <button
                    onClick={onStartMoot}
                    className="bg-fg hover:opacity-90 text-canvas rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                  >
                    启动模拟法庭压力测试 →
                  </button>
                ) : (
                  <p className="text-xs text-muted">完成主诉评估后可启动（红线拦截同样不可启动）</p>
                )}
              </div>
            ) : activeGroup.id === 'eval-business' ? (
              // 业务预期：总览卡已删（目标见头部 chip、子维度见小标题），chip 只切换子维度（判赔规模/回款能力；要名为判例价值）
              subNodes.length > 1 ? (
                <AnimatePresence mode="wait">
                  <motion.div key={activeGroup.id + ':' + currentNode} {...STEP_MOTION}>
                    {nodeCard(currentNode, renderDetail(currentNode), { skipIfBlocked: true, rerunnable: true })}
                  </motion.div>
                </AnimatePresence>
              ) : (
                subNodes.map((sub) => (
                  <div key={sub}>
                    {nodeCard(sub, renderDetail(sub), { skipIfBlocked: true, rerunnable: true })}
                  </div>
                ))
              )
            ) : activeGroup.nodes.length > 1 ? (
              // 多节点轴（前置盘点/法律可行性）：一次只显示当前维度一张卡（占满全宽），切换动画与切轴一致（复用 STEP_MOTION）
              <AnimatePresence mode="wait">
                <motion.div key={activeGroup.id + ':' + currentNode} {...STEP_MOTION}>
                  {nodeCard(currentNode, renderDetail(currentNode), {
                    rerunnable: currentNode !== 'synthesize',
                    skipIfBlocked: true,
                  })}
                </motion.div>
              </AnimatePresence>
            ) : (
              // 单节点轴（决策合成）：整排渲染，不加切换
              <div className={cn('grid gap-3', activeGroup.cols)}>
                {activeGroup.nodes.map((node) => (
                  <div key={node}>
                    {nodeCard(node, renderDetail(node), {
                      rerunnable: node !== 'red_gate' && node !== 'synthesize',
                      skipIfBlocked: true,
                    })}
                  </div>
                ))}
              </div>
            )}
          </section>
        </motion.div>
      </AnimatePresence>
    </>
  )
}
