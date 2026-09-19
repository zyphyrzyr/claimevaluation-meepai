import { useState, useMemo, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { STEP_MOTION } from '../lib/motion'
import { cn } from '../lib/utils'
import { type Thresholds, type NodeState } from '../lib/tiers'
import {
  damagesParts,
} from '../lib/damagesWording'
import { Card } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { StepperNode } from '../components/ui/StepperNode'
import { buildTrace } from '../components/EvalTrace'
import RunControlBar from '../components/RunControlBar'
import MootPanel, {
  type MootJudgeInfo,
  type MootScoresUpdated,
} from '../components/MootPanel'
import type { EvalEvent, MootRound } from '../api'
import { Basis, BasisList, sevPill, SEV_LABEL, signalMeaning } from '../components/Basis'

// 流程顺序与后端 NODE_ORDER 对齐（orchestrator.py）
// 导出给 CaseWorkbench：运行控制条用它算「第 N / 7 步」，避免两边各写一份顺序而走歪。
export const NODE_ORDER = [
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
/**
 * 模拟法庭的运行时状态。
 *
 * 由父级（CaseWorkbench）持有而不是住在本组件里：本组件是「单块逐步」渲染，
 * 切一次轴就卸载一次，庭审跑到一半切走再切回来会整场清零。
 */
export interface MootState {
  /** 内嵌 = 评估后压力测试（系数回写）；独立演练 = 纯演练不回写 */
  mode: 'embedded' | 'standalone'
  /** 模型已生成的全部轮次（raw）：节奏控制缓冲区的"源" */
  rounds: MootRound[]
  /** 已揭示（显示）的轮次：缓冲区按 pace 从 rounds 里逐条放出，CourtRoom 只渲染这些 */
  shownRounds: MootRound[]
  running: boolean
  /** 本次被用户中止：保留已说轮次，但不回写、不落库 */
  stopped: boolean
  judge: MootJudgeInfo | null
  scoresUpdated: MootScoresUpdated | null
  error: string
  /** 播放节奏：speed = 每轮揭示间隔（毫秒），paused = 暂停揭示（模型仍在跑） */
  pace: { speed: number; paused: boolean }
}

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
  // 模拟法庭不在自动流程里（手动 opt-in 的庭审对抗），nodes 空 = 无 chip，选中时走专属面板分支
  { id: 'eval-moot', label: '模拟法庭（可选）', axis: '模拟法庭', nodes: [], cols: '' },
]

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

/**
 * 材料标题瘦身。
 * 库里存的是「证据材料-1:【证据文件: 证据1-商标注册证（第110630号）…」这种带前缀和
 * 内容摘要的长串，原样铺开既读不了、也对不上用户上传的文件名，所以只留可读的短名。
 */
function shortMaterialTitle(t: any): string {
  const s = String(t ?? '')
  if (!s) return '未命名材料'
  let out = s.replace(/^[^:：]*[:：]\s*/, '')
  out = out.replace(/^【[^】]*】\s*/, '')
  out = out.replace(/（[^（）]*…?$/, '')
  return out.length > 24 ? `${out.slice(0, 24)}…` : out
}
function riskColor(l?: string) {
  if (l === 'high') return 'bg-[var(--danger-soft)] text-[var(--danger)]'
  if (l === 'medium') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  if (l === 'low') return 'bg-[var(--success-soft)] text-[var(--success)]'
  return 'bg-surface text-muted'
}
function fmtDur(ms?: number): string {
  if (!ms || ms < 0) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/**
 * 单个节点完成后：在结果卡内折叠展示本次评估的过程（步骤/耗时），默认折叠。
 * 运行中节点由 nodeCard 内联实时步骤，这里只补「已完成」的回看。
 * traceEvents 仅在当次会话评估时被捕获；从历史结果进入则为空，组件返回 null。
 */
function NodeProcess({
  node,
  traceEvents,
  states,
}: {
  node: string
  traceEvents: EvalEvent[]
  states: Record<string, NodeState>
}) {
  const groups = useMemo(() => buildTrace(traceEvents, states), [traceEvents, states])
  const g = groups.find((x) => x.node === node)
  const [open, setOpen] = useState(false)
  if (!g || g.items.length === 0) return null
  return (
    <div className="mt-3 border-t border-line pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 text-left"
      >
        <span className="text-xs text-muted">
          过程 · 已完成 {g.items.length} 步{g.durationMs ? ` · 累计 ${fmtDur(g.durationMs)}` : ''}
        </span>
        <span className="flex-1" />
        <span className="text-xs text-muted">{open ? '收起' : '展开回看'}</span>
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {g.items.map((it, i) => (
            <li key={i} className="text-sm">
              <div className="flex gap-2 items-start">
                {it.kind === 'mcp' ? (
                  <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded border border-line text-[11px] text-muted">
                    {it.vendor ?? '外部数据'}
                  </span>
                ) : (
                  <span className="shrink-0 mt-[7px] w-1.5 h-1.5 rounded-full bg-line" />
                )}
                <span className={cn('text-muted', it.kind === 'mcp' && 'text-fg')}>{it.text}</span>
              </div>
              {it.detail && (
                <p className="text-xs text-muted mt-0.5 ml-[14px] whitespace-pre-line">{it.detail}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * 参考材料准备（__recall__）是全局过程，不在某个轴内：在工作区内容顶部内联一条轻量提示，
 * 替代原独立「评估过程」卡片里的同名分组。
 */
function InlineRecall({
  traceEvents,
  states,
}: {
  traceEvents: EvalEvent[]
  states: Record<string, NodeState>
}) {
  const groups = useMemo(() => buildTrace(traceEvents, states), [traceEvents, states])
  const g = groups.find((x) => x.node === '__recall__')
  if (!g) return null
  // 头部直接用事件带出的材料数（g.count），不再数 trace 事件条数，
  // 避免「头部 1 条 / 正文 8 条」的自相矛盾。
  const n = typeof g.count === 'number' ? g.count : g.items.length
  return (
    <div className="mb-4 rounded-xl border border-line bg-surface px-5 py-3 flex items-center gap-2">
      <span className="text-sm font-medium text-fg">参考材料准备</span>
      <span className="text-xs text-muted">已自动召回 {n} 条</span>
    </div>
  )
}

/**
 * 评估详情（运行/完成时间线）。运行时状态由父级 CaseWorkbench 持有并下发，
 * 本组件只负责渲染；节点级重跑与「查看完整评估结果」通过回调上抛。
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
  onStopMoot,
  moot,
  traceEvents,
  voided,
  onMootPaceChange,
  onMootTogglePause,
  onMootStep,
  /** 评估运行控制条（暂停/继续/终止）透传：从父级 CaseWorkbench 移入轴标题栏右侧 */
  runDone,
  runTotal,
  onRunPause,
  onRunResume,
  onRunStop,
  /** 评估结果加载失败信息（容器层静默失败兜底）；非空时展示错误与重试 */
  resultError,
  /** 重试加载评估结果 */
  onRetryResult,
}: {
  caseId: string
  result: any
  states: Record<string, NodeState>
  phase: 'prep' | 'running' | 'paused' | 'done'
  finished: string
  error: string
  /** 当前展示的轴（EVAL_AXES 的 id） */
  activeAxis: string
  /** 切换轴：左侧导航列与窄屏分段控件都走它 */
  onAxisChange: (id: string) => void
  onStart?: () => void
  onViewResult: () => void
  onRerun: (node: string, guidance: string) => Promise<void>
  /** 启动模拟法庭：就地在本轴开庭，不再跳转独立页面 */
  onStartMoot: () => void
  /** 中止进行中的庭审：当前这轮说完后停止，不回写系数 */
  onStopMoot: () => void
  /** 模拟法庭运行时状态（父级持有） */
  moot: MootState
  /** 评估过程事件流（node_step / mcp_call / 节点起止），由父级持有，避免切标签时丢失 */
  traceEvents: EvalEvent[]
  /** 本次评估被「终止」作废：已完成的节点结果已全部清空，需提示用户可重新评估 */
  voided?: boolean
  /** 模拟法庭播放节奏控制：调速度 */
  onMootPaceChange: (speed: number) => void
  /** 模拟法庭播放节奏控制：暂停/继续揭示 */
  onMootTogglePause: () => void
  /** 模拟法庭播放节奏控制：单步（立即放出下一轮，暂停时也有效） */
  onMootStep: () => void
  /** 评估运行控制条（暂停/继续/终止）透传 */
  runDone: number
  runTotal: number
  onRunPause: () => void
  onRunResume: () => void
  onRunStop: () => void
  /** 评估结果加载失败信息（容器层静默失败兜底）；非空时展示错误与重试 */
  resultError?: string | null
  /** 重试加载评估结果 */
  onRetryResult?: () => void
}) {
  const [rerunTarget, setRerunTarget] = useState<string | null>(null)
  const [rerunGuidance, setRerunGuidance] = useState('')
  const [rerunBusy, setRerunBusy] = useState(false)
  // 轴内维度选中态。**必须声明在所有 return 之前**：下面是「未开始评估」的提前返回，
  // 首次加载时 result 为空会命中它；若把本 hook 放在提前返回之后，等 result 到达时就变成
  // 「Rendered more hooks than during the previous render」→ 整页白屏。
  // 刷新「评估详情」直接落到 run 标签页时必现（走左侧导航切过去时 result 已加载，故不触发）。
  const [activeNode, setActiveNode] = useState<string | null>(null)

  const thresholds: Thresholds =
    result?.thresholds ?? {
      go: 78, patch: 62, quadrant_mid: 78, power_mean_p: -0.5,
      recovery_base: 78, recovery_ok: 70, recovery_weak: 45,
    }
  const goalType: string = result?.goal_type ?? '要钱'
  const blocked = Boolean(result?.dimension_results?.red_gate?.result?.blocked)
  // 模拟法庭状态（与评估结果页同口径：correction_coeff 存在且 ≠1 视为已回写）
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
  // 例外：模拟法庭轴。独立演练不要求先跑评估，未评估的案件也要能就地开庭——
  // 若这里一并挡掉，「仅开始模拟法庭」这条入口对草稿案件就是死的。
  if (!result && phase === 'prep' && activeAxis !== 'eval-moot') {
    // 评估结果没加载出来：区分「真没评估过」与「加载失败」。失败要明确报错 + 给重试，
    // 否则就会表现为「所有评估都空详情」且控制台毫无痕迹。
    if (resultError) {
      return (
        <div className="bg-[var(--danger-soft)] border border-[var(--danger)] rounded-xl p-8 text-center">
          <p className="text-[var(--danger)] text-sm mb-3 font-medium">评估结果加载失败</p>
          <p className="text-muted text-sm mb-4">{resultError}</p>
          {onRetryResult && (
            <button
              onClick={onRetryResult}
              className="bg-fg hover:opacity-90 text-canvas px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
            >
              重试加载
            </button>
          )}
        </div>
      )
    }
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
  // （activeNode 的 useState 已上移到提前返回之前，见上方注释。）
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

    // 重跑输入面板。此前点「重跑」是**整块替换正文**——卡片只剩输入框，重跑前的旧结论
    // 全被顶掉，用户没法边看旧结论边写引导意见（2026-09-19 反馈）。
    // 现改为正文前置块：表单在上、旧结果原样留在下面，并注明下方是重跑前结果。
    const rerunForm = (
      <div className="mb-3 rounded-lg border border-line bg-surface p-3">
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          <span className="text-xs font-medium text-fg">重跑「{label}」</span>
          <span className="text-[11px] text-muted">可选：补充引导意见，将注入后续所有节点</span>
        </div>
        <textarea
          value={rerunGuidance}
          onChange={(e) => setRerunGuidance(e.target.value)}
          placeholder="例：对方商标 2024 年已被提撤三，请据此重新评估权利稳定性。"
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

    // 先把「正文」（不含重跑表单）算出来，再把表单作为前置块拼上去，
    // 保证重跑输入与旧结果同屏可见。
    let detailBody: ReactNode
    if (isSkipped) {
      detailBody = <p className="text-sm text-muted">未执行（被前置红线拦截）</p>
    } else if (status === 'running' && !d) {
      // 运行中不再只显示「计算中…」——把这一环节已经走过的步骤实时列出来
      const steps = traceEvents.filter(
        (e) => (e.event === 'node_step' || e.event === 'mcp_call') && e.node === node,
      )
      detailBody = steps.length > 0 ? (
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
      // 评估进行中：该环节还没轮到（不再误显示「待前面评估完成后自动开始」）
      // 评估已完成却仍无结果：说明本次未产出该环节，可重跑，而非「等前面」
      detailBody = phase === 'running' || phase === 'paused'
        ? <p className="text-sm text-muted">等待前序环节…</p>
        : <p className="text-sm text-muted">本次未产出该环节结果（可在本轴重跑）</p>
    } else {
      detailBody = (
        <>
          {extra}
          {phase === 'done' && (
            <NodeProcess node={node} traceEvents={traceEvents} states={states} />
          )}
        </>
      )
    }

    const body = (rerunTarget === node && !isSkipped) ? (
      <>
        {rerunForm}
        {d && (
          <p className="text-[11px] text-muted mb-2">
            下方为本次重跑前的旧结果；确认重跑后将自动刷新。
          </p>
        )}
        {detailBody}
      </>
    ) : detailBody

    return <StepperNode status={status} label={label} right={right}>{body}</StepperNode>
  }

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

  // 三个法律维度的依据区块，只回答两件事：本维度依赖哪些证据要件、本维度为什么得这个分。
  // 幂平均的完整口径与「参考材料」清单各自只讲一次（前者在决策合成分，后者在证据盘点列明细），
  // 四个维度不再各抄一遍，界面因此干净很多。
  const legalBasis = (label: string, score: any, categories: string[] = [], pkulaw?: any) => {
    const corr = result?.correction_coeff
    return (
      <BasisList>
        <Basis title="证据依据">
          <EvItems categories={categories} />
        </Basis>
        <Basis
          title="评分依据"
          hint="法律可行性 = 权利基础、侵权认定、诉讼程序三个维度的幂平均（p<0，短板主导：任一维度低，整体就被拉下来，不像算术平均能被高分掩盖）。再乘模拟法庭的修正系数（0.7–1.3）。"
        >
          本维度 {fmtNum(score)} 分，与另外两个法律维度合成法律可行性
          {corr != null && corr !== 1 ? `，并已乘上模拟法庭修正系数 ${corr}` : ''}。
        </Basis>
        {/* 北大法宝外部检索依据：必须与其余依据同处「判断依据」折叠面板内，
            默认收起、展开后一并显示；此前挂在 BasisList 之外，导致面板收起时它仍常驻可见。 */}
        {pkulaw && <PkulawBasis pk={pkulaw} />}
      </BasisList>
    )
  }

  /**
 * 长文本截断 + 展开。
 * 法条与类案摘要原文动辄几百字，整段铺开会把真正要看的本案结论挤到屏幕外；
 * 默认只给一眼能读完的部分，想看全文再点开。
 */
function Clamped({ text, limit = 90 }: { text: string; limit?: number }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  const long = text.length > limit
  return (
    <span>
      {long && !open ? `${text.slice(0, limit)}…` : text}
      {long && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="ml-1 text-[11px] text-fg underline underline-offset-2"
        >
          {open ? '收起' : '展开'}
        </button>
      )}
    </span>
  )
}

// 北大法宝外部检索依据（问题2 修复）：法律可行性三节点接入外部法律数据库，
  // 把检索到的法条 / 类案显式透出，让评判「有外部依据」可见，而非只给一个分数。
  const PkulawBasis = ({ pk }: { pk?: any }) => {
    if (!pk) return null
    const laws: any[] = pk.laws ?? []
    const cases: any[] = pk.cases ?? []
    if (pk.status === 'error' || pk.error) {
      return (
        <Basis title="外部检索依据（北大法宝）">
          <span className="text-sm text-[var(--danger)]">
            检索未成功：{pk.error ?? '未知错误'}。本次评判缺少外部法条/类案佐证，建议检查北大法宝配置后重跑本节点。
          </span>
        </Basis>
      )
    }
    if (laws.length === 0 && cases.length === 0) {
      return null
    }
    return (
      <Basis title="外部检索依据（北大法宝）">
        <p className="text-xs text-muted mb-1.5">{pk.summary || '已检索北大法宝作为外部法律参照（不替代权威来源）。'}</p>
        {laws.length > 0 && (
          <div className="space-y-1">
            {laws.slice(0, 5).map((l: any, i: number) => (
              <div key={i} className="text-sm text-muted">
                <b className="text-fg">{l.title}</b>
                {l.content ? <>：<Clamped text={String(l.content)} /></> : ''}
              </div>
            ))}
          </div>
        )}
        {cases.length > 0 && (
          <div className="space-y-1 mt-1.5">
            {cases.slice(0, 4).map((c: any, i: number) => (
              <div key={i} className="text-sm text-muted">
                <b className="text-fg">{c.title}</b>
                {c.court || c.ahao ? `（${[c.court, c.ahao].filter(Boolean).join(' ')}）` : ''}
                {c.summary ? <>：<Clamped text={String(c.summary)} limit={120} /></> : ''}
              </div>
            ))}
          </div>
        )}
      </Basis>
    )
  }


  // 企查查 8 阶段结果 → 一句话摘要。此前前端只 Object.keys() 数了阶段个数，
  // 接口早就返回了 stages 明细却没人读（评测 P1-3）。
  const qccStageSummary = (key: string, data: any): string => {
    if (data == null) return '—'
    if (key === 'A_主体锁定') {
      return data.ok
        ? `已锁定：${data.locked_name ?? '—'}（${data.match_status || '已匹配'}）`
        : `未锁定：${data.error ?? '无候选主体'}`
    }
    // C 阶段：hits 是「维度 → 命中条数」。此前 hits 对象取不到摘要值被过滤掉，
    // 只剩 total_hit_dimensions 这个内部字段名被拼进文案（界面上显示
    // 「风险整体分诊 total_hit_dimensions 3」），而真正要看的「命中了哪几个维度」反而丢了。
    if (key.startsWith('C_')) {
      const dims = Object.keys(data.hits ?? {}).filter((k: string) => !k.startsWith('_'))
      const n = data.total_hit_dimensions ?? dims.length
      if (n > 0) {
        return `命中 ${n} 个风险维度`
          + (dims.length ? `：${dims.slice(0, 5).join('、')}${dims.length > 5 ? ' 等' : ''}` : '')
      }
      return String(data._summary ?? '未命中风险维度')
    }
    if (typeof data === 'string') return data
    if (!Array.isArray(data) && typeof data === 'object') {
      const counts = Object.entries(data)
        .filter(([k]) => !k.startsWith('_'))
        .map(([k, v]: [string, any]) =>
          [k, (v && typeof v === 'object' ? (v._summary ?? v._count) : v)])
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
      const nonZero = counts.filter(([, v]) =>
        typeof v === 'number' ? v > 0 : !/^0(条|件)?$/.test(String(v)))
      if (nonZero.length > 0) return nonZero.map(([k, v]) => `${k} ${v}`).join('；')
      if (counts.length > 0) return `${counts.length} 个维度全部为 0 条`
      if (data._summary) return String(data._summary)
    }
    return String(data._summary ?? '已获取')
  }

  // 企查查外部事实展示：逐条列出真正查到的工商字段。
  // 原实现是一段规则说明书 + 硬编码分档阈值，只插了 damages_adjustment 一个变量，
  // 还写「据此给出判赔调整」——而那个标签在代码里从未参与任何金额计算，因果链是虚构的。
  const QccFacts = ({ facts, simulated }: { facts?: any; simulated?: boolean }) => {
    const e = facts?.entity ?? {}
    const s = facts?.scale ?? {}
    const rows: { label: string; value: string }[] = []
    // 0 也是查到的事实（「商标 0 件」「线上渠道 0 个」恰恰能驳斥材料里的规模主张），
    // 所以只跳过 null/空串，不能跳过 0。
    const put = (label: string, v: any, suffix = '') => {
      if (v === null || v === undefined || v === '') return
      rows.push({ label, value: `${v}${suffix}` })
    }
    put('统一社会信用代码', e.credit_code)
    put('法定代表人', e.legal_rep)
    put('登记状态', e.reg_status)
    put('成立日期', e.established)
    put('注册资本', e.registered_capital)
    put('参保人数', e.insured_count, ' 人')
    put('人员规模标注', e.staff_scale)
    put('所属行业', e.industry)
    put('所在地', e.region)
    put('商标资产', s.trademark_count, ' 件')
    put('被诉历史', s.litigation_history, ' 件')
    const channels: any[] = [s.online_shops, s.app, s.miniprogram, s.wechat_mp, s.douyin]
    if (channels.some((c) => c != null)) {
      const total = channels.reduce((a, b) => a + (Number(b) || 0), 0)
      rows.push({
        label: '线上渠道',
        value: `${total} 个（店铺 ${s.online_shops ?? 0}、APP ${s.app ?? 0}、小程序 ${s.miniprogram ?? 0}、公众号 ${s.wechat_mp ?? 0}、抖音 ${s.douyin ?? 0}）`,
      })
    }

    return (
      <div className="space-y-1.5">
        {simulated && (
          <div className="text-xs px-2 py-1 rounded bg-[var(--warning-soft)] text-[var(--warning)] inline-block">
            以下为模拟数据（USE_MOCK=True），并非企查查真实查询结果
          </div>
        )}
        {e.name_matches_query === false && (
          <div className="text-sm text-[var(--danger)]">
            ⚠️ 实际锁定的主体「{e.name || e.locked_name || '未知'}」与所查名称「{e.queried_name}」不一致，
            下列事实可能属于另一家公司，请核实被告名称后重跑本节点。
          </div>
        )}
        {rows.length === 0 ? (
          <div className="text-sm">
            本次未取到被告的工商登记事实（企查查未配置、未查询或查询失败），
            这一步的判断完全依据案情材料。
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-6 gap-y-1">
            {rows.map((row) => (
              <div key={row.label} className="text-sm flex gap-2">
                <span className="text-muted shrink-0 w-28">{row.label}</span>
                <span className="text-fg min-w-0 break-all">{row.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>
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
              <Basis
                title="证据依据"
                hint="完整度算法：齐备记 1 分、不足记 0.5 分、缺失记 0 分，取平均后换算成百分比。每项要件都带法律出处。这份清单同时决定红线检查中「权利证明」「侵权证据」「损失证据」三条规则能否放行。"
              >
                共 {matrix.length} 项要件，其中 {sufficient.length} 项齐备
                {lacking > 0 ? `、${lacking} 项不足或缺失` : ''}，
                证据完整度 <b className="text-fg">{r.completeness ?? 0}%</b>。
              </Basis>
              {recalled.length > 0 && (
                <Basis
                  title="参考材料"
                  hint="系统按「案由 + 业务目标 + 案情描述」自动检索本案材料库与全局经验库，命中项会注入后续每个判断环节的提示词，用户无需手动勾选。"
                >
                  自动召回 {recalled.length} 条：
                  {recalled.slice(0, 3).map((m: any) => shortMaterialTitle(m.title)).join('、')}
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
        // 判断依据要说清「哪几条没过、为什么」，而不是把六条规则的定义和图例再抄一遍——
        // 规则清单与「通过/警示/拦截」的含义是常量，放进标题旁的小问号即可。
        const hits: any[] = r.hits ?? []
        const nPass = hits.filter((h: any) => h.severity === 'pass').length
        const nWarn = hits.filter((h: any) => h.severity === 'warning').length
        const nBlock = hits.filter((h: any) => h.severity === 'block').length
        const warnHits = hits.filter((h: any) => h.severity === 'warning')
        const blockHits = hits.filter((h: any) => h.severity === 'block')
        // 这三条规则不是独立判断的，它们的输入正是证据盘点的结果
        const evidenceFed = hits.filter((h: any) =>
          ['权利证明', '侵权证据', '损失证据'].some((k) => (h.rule_name ?? '').includes(k)),
        )
        const GATE_HINT =
          '六条程序性规则：诉讼时效、主体资格、仲裁条款、权利证明、侵权证据、损失证据。'
          + '判定标准写死在引擎里、不调用模型，所以同一份材料重跑结果完全一致。'
          + '通过 = 不构成障碍；警示 = 可继续推进，但建议补强；拦截 = 硬性障碍，评估就此终止。'
        const gateBasis = (
          <BasisList>
            <Basis title="规则依据" hint={GATE_HINT}>
              {hits.length} 条规则走完：
              <b className="text-fg">{nPass} 条通过</b>
              {nWarn > 0 ? <>、<b className="text-[var(--warning)]">{nWarn} 条警示</b></> : null}
              {nBlock > 0 ? <>、<b className="text-[var(--danger)]">{nBlock} 条拦截</b></> : null}
              。
              {warnHits.length > 0 && (
                <div className="mt-1">
                  警示项：
                  {warnHits.map((h: any) => (
                    <span key={h.rule_code} className="mr-2">
                      {h.rule_name}
                      {h.reason ? `（${h.reason}）` : ''}
                    </span>
                  ))}
                </div>
              )}
              {blockHits.length > 0 && (
                <div className="mt-1">
                  拦截项：
                  {blockHits.map((h: any) => (
                    <span key={h.rule_code} className="mr-2">
                      {h.rule_name}
                      {h.reason ? `（${h.reason}）` : ''}
                    </span>
                  ))}
                </div>
              )}
            </Basis>
            <Basis
              title="证据依据"
              hint="其中「权利证明」「侵权证据」「损失证据」三条不独立判断，输入正是证据盘点的核对结果：材料齐备即放行、缺失即拦截。"
            >
              {evidenceFed.length > 0
                ? `「${evidenceFed.map((h: any) => h.rule_name).join('」「')}」这 ${evidenceFed.length} 条读取了证据盘点结果（当前完整度 ${r.completeness ?? ev.completeness ?? 0}%）。`
                : `本步不单独读材料，输入来自证据盘点与案情描述（当前完整度 ${ev.completeness ?? 0}%）。`}
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
                    <span className={cn('shrink-0 self-start mt-0.5 w-12 text-center px-1 rounded text-xs', sevPill(h.severity))}>{SEV_LABEL[h.severity] ?? h.severity}</span>
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
                  <span className={cn('shrink-0 self-start mt-0.5 w-12 text-center px-1 rounded text-xs', sevPill(h.severity))}>{SEV_LABEL[h.severity] ?? h.severity}</span>
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
            {legalBasis('权利基础', r.score, ['权利基础证据'], r.pkulaw)}
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
            {legalBasis('侵权认定', r.score, ['侵权认定证据', '取证技术规范'], r.pkulaw)}
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
            {legalBasis('诉讼程序', r.score, ['取证技术规范'], r.pkulaw)}
          </div>
        )
      case 'damages':
        return (
          <div>
            <ScoreBadge score={r.score} t={t} />
            <p className="text-sm text-muted mt-2 leading-relaxed">
              {damagesParts(r, true).map((p, i) =>
                p.bold ? (
                  <b key={i} className="text-fg">{p.text}</b>
                ) : (
                  <span key={i}>{p.text}</span>
                )
              )}
            </p>
            <BasisList>
              <Basis title="证据依据">
                <EvItems categories={['损害赔偿证据']} />
              </Basis>
              <Basis
                title="规则与算法依据"
                hint="推导顺序：先以本案由的法定赔偿区间划定上下边界，再参照同类案件判赔水平定中位锚点，最后结合案情里的侵权规模推出三档金额。回报倍数 ＝ 最可能的判赔额 ÷ 预估总成本（律师费、诉讼费、公证取证费等，约 8–15 万元）。本维度与回款能力按短板效应合成业务预期：判得再多收不回来也白搭，收得回来但判得太少同样不划算。"
              >
                判赔额按「法定区间 → 同类案件水平 → 本案侵权规模」三步推得，本维度 {fmtNum(r.score)} 分。
              </Basis>
              <Basis title="外部数据依据">
                <QccFacts facts={qccMetrics.facts} simulated={qccMetrics.simulated} />
                {qccMetrics.facts?.scale_tier && (
                  <p className="mt-1.5">
                    被告规模档位：<b className="text-fg">{qccMetrics.facts.scale_tier}</b>
                    （{qccMetrics.facts.scale_tier_basis}），仅用于核对被告体量，
                    <b className="text-fg">不参与判赔金额计算</b>。
                  </p>
                )}
                {(qccMetrics.time_extra_months ?? 0) > 0 && (
                  <p className="mt-1.5">
                    预计诉讼周期比常规多约 <b className="text-fg">{qccMetrics.time_extra_months}</b> 个月
                    （由被诉历史、终本案件、被执行次数等累加）。
                  </p>
                )}
              </Basis>
              {r.external_conflict && (
                <Basis title="材料与外部数据的矛盾">
                  <span className="text-[var(--warning)]">{r.external_conflict}</span>
                  <p className="mt-1.5">
                    已据此下调对侵权规模的采信程度（见上方「规模支撑度」），判赔额按工商客观数据校正后的规模重算。
                  </p>
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
              <ScoreBadge score={r.recovery_ability} t={t} kind="recovery" />
            ) : (
              <span className="text-xs text-muted">未获取到被告企业画像，回款能力无法计算（业务预期将标注未完成）</span>
            )}
            {/* 基准说明并入正文同一段：它是口径说明而非异常，不值得单独一块。
                判定条件只看 signals_hit（规则表消费过的加扣分项）——
                基准 78 之后「无信号」的含义仍是「没有下调依据」，要跟用户说清。 */}
            <p className="text-sm text-muted mt-2 leading-relaxed">
              {r.recovery_ability != null ? (
                <>
                  赢了官司不等于拿得到钱。系统按被告的工商状态与涉诉记录，判断胜诉后实际能够收回款项的可能性约为{' '}
                  {fmtNum(r.recovery_ability)}%。
                  {(qccMetrics.facts?.signals_hit ?? 0) === 0 && (
                    qccMetrics.time_only_hits?.length > 0
                      ? ` 本次只查到 ${qccMetrics.time_only_hits.join('、')}，这类记录只计入诉讼周期（预估 +${qccMetrics.time_extra_months ?? 0} 个月），不扣减回款能力——${fmtNum(r.recovery_ability)} 分是「公开记录查不到偿付问题」的默认取值。`
                      : ` 本次查询没有命中任何加扣分项，${fmtNum(r.recovery_ability)} 分是规则表对「公开记录查不到问题」的默认取值，不代表已核实被告具备偿付能力。`
                  )}
                </>
              ) : (
                '缺少被告的企业画像，这一步无法给出回款结论，业务预期会因此标记为未完成。'
              )}
            </p>
            <BasisList>
              <Basis
                title="外部数据依据"
                hint="查询口径：先按名称模糊搜索候选主体，再用行业与所在地消歧、锁定被告本体，避免查错公司。全程不调用大模型。"
              >
                数据来自企查查被告画像，本次取到 {stageKeys.length} / 8 个环节
                {stageKeys.length > 0 ? `：${stageKeys.map(qccStageLabel).join('、')}。` : '（未取到阶段明细）。'}
              </Basis>
              {stageKeys.length > 0 && (
                <Basis title="8 环节明细">
                  <div className="space-y-1">
                    {stageKeys.map((k: string) => {
                      const line = qccStageSummary(k, qccStages[k])
                      // 「已获取 / —」这类没有信息量的行不占版面：读了等于没读
                      if (!line || line === '—' || line === '已获取') return null
                      return (
                        <div key={k} className="text-sm flex gap-2 items-start">
                          <span className="shrink-0 text-fg w-24">{qccStageLabel(k)}</span>
                          <span className="text-muted min-w-0 break-words">{line}</span>
                        </div>
                      )
                    })}
                  </div>
                </Basis>
              )}
              <Basis
                title="规则与算法依据"
                hint={`从 ${fmtNum(t.recovery_base ?? 78)} 分起算：失信 −60、被执行≥3次 −35、终本≥3次 −25、严重违法 −20、经营异常 −12、核心资产冻结质押≥3类 −45、欠税等轻微项各 −5（合计封顶 −10）；上市 +10、财务公开 +6、实控人有可追溯资产 +5。出现注销 / 清算 / 破产重整则直接归零。加减后截断在 0–100。规则写死在引擎里，同一份材料重跑结果完全一致。`}
              >
                {qccMetrics.recovery_delta != null && qccMetrics.recovery_delta !== 0
                  ? `本次共命中加扣分项，合计 ${qccMetrics.recovery_delta > 0 ? '+' : ''}${fmtNum(qccMetrics.recovery_delta)} 分，
                     ${fmtNum(t.recovery_base ?? 78)} → ${fmtNum(r.recovery_ability)} 分。`
                  : `本次未命中任何加扣分项，维持在基准 ${fmtNum(r.recovery_ability)} 分。`}
              </Basis>
              <Basis title="风险与利好信号">
                {r.red_flags?.length > 0 || r.green_flags?.length > 0 ? (
                  <div className="space-y-1.5">
                    <div>
                      本次一共命中 {r.red_flags?.length ?? 0} 项不利迹象、{r.green_flags?.length ?? 0} 项有利迹象，逐项含义如下：
                    </div>
                    <ul className="space-y-1">
                      {(r.red_flags ?? []).map((f: string, i: number) => (
                        <li key={`r${i}`} className="flex gap-2 items-start">
                          <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-[var(--danger)]" />
                          <span>{signalMeaning(f, 'red')}</span>
                        </li>
                      ))}
                      {(r.green_flags ?? []).map((f: string, i: number) => (
                        <li key={`g${i}`} className="flex gap-2 items-start">
                          <span className="shrink-0 mt-1.5 w-1.5 h-1.5 rounded-full bg-[var(--success)]" />
                          <span>{signalMeaning(f, 'green')}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  '未命中任何加扣分项。'
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
        const legal = syn.scores?.legal_feasibility
        const biz = syn.scores?.business_expectation
        return (
          <div>
            {/* 与其他节点一致：结论分直接亮徽标，总分 + 两个上游分一眼可比 */}
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
              <span className="flex items-center gap-1.5">
                <ScoreBadge score={syn.scores?.final} t={t} />
                <span className="text-xs text-muted">主诉决策分</span>
              </span>
              <span className="flex items-center gap-1.5">
                <ScoreBadge score={legal} t={t} />
                <span className="text-xs text-muted">法律可行性</span>
              </span>
              <span className="flex items-center gap-1.5">
                <ScoreBadge score={biz} t={t} />
                <span className="text-xs text-muted">业务预期</span>
              </span>
            </div>
            <p className="text-sm text-muted leading-relaxed mt-2">
              主诉决策分 {fmtNum(syn.scores?.final)}，由法律可行性 {fmtNum(legal)} 与业务预期 {fmtNum(biz)}
              按短板效应合成——法律上站得住但收不回钱，或反过来，都不足以支撑起诉。
              两步合成的逐步算式见下方「判断依据」。
            </p>
            {syn.missing?.length > 0 && (
              <div className="text-sm text-[var(--warning)] mt-2">
                未产出维度：{syn.missing.join('、')}。这些维度没有拿到分数，因此不会参与合成，
                结论的完整度会因此打折。
              </div>
            )}
            <BasisList>
              <Basis title="评分依据">
                <div className="space-y-1.5">
                  <div>
                    <b className="text-fg">法律可行性</b>：把权利基础 {fmtNum(dimScore('rights'))} 分、
                    侵权认定 {fmtNum(dimScore('infringement'))} 分、诉讼程序 {fmtNum(dimScore('procedure'))} 分，
                    三者经<b>幂平均</b>合成
                    {mootDone
                      ? `，再乘上模拟法庭给出的修正系数 ${mootCoeff}`
                      : '（尚未跑模拟法庭，按系数 1.0 计入）'}
                    ，得 {fmtNum(syn.scores?.legal_feasibility)} 分。
                    用幂平均而不是算术平均，是为了体现短板效应：三个维度里只要有一个明显偏低，整体就会被拉下来，
                    不会被另外两个的高分「平均」掉——这与法官「任一要件不成立即败诉」的直觉一致。
                  </div>
                  <div>
                    <b className="text-fg">业务预期</b>：
                    {goalIsMoney
                      ? `判赔规模 ${fmtNum(dimScore('damages'))} 分与回款能力 ${fmtNum(dimScore('recovery'))} 分，同样经幂平均合成，得 ${fmtNum(syn.scores?.business_expectation)} 分。它同样是短板效应：判得再多、收不回来也白搭；收得回来但判得太少，同样不划算。`
                      : `「要名」目标下不看钱，直接取判例价值 ${fmtNum(dimScore('precedent'))} 分——衡量的是规则影响力。`}
                  </div>
                  <div>
                    <b className="text-fg">主诉决策分</b>：把法律可行性与业务预期再经一次幂平均，得{' '}
                    {fmtNum(syn.scores?.final)} 分。法律上站得住但收不回钱，或反过来，都不足以支撑起诉。
                  </div>
                </div>
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
      {voided && (
        <div className="bg-[var(--danger-soft)] border border-[var(--danger)] rounded-lg p-3 mb-4 text-sm">
          <div className="font-medium text-[var(--danger)]">本次评估已终止并作废</div>
          <p className="text-muted mt-1">
            终止时所有已完成的节点结果均未保留，案件已回到未评估状态。可点击上方「开始评估」重新启动。
          </p>
        </div>
      )}
      {/* 评估过程不再用独立卡片：参考材料准备（__recall__）内联为一条轻量提示，
          各节点的过程在对应结果卡内折叠展示（见 NodeProcess）。仅当本次会话真的
          捕获到事件时才占位，刷新后事件流为空则整体不占地方。 */}
      {traceEvents.length > 0 && (
        <InlineRecall traceEvents={traceEvents} states={states} />
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

      {/* 案件已评估、但后端没返回任何维度结果：多半是被「编辑案件 / 仅开始模拟法庭」
          误清空，或评估中途异常未落库。明确提示，而不是让每个节点都显示「未产出」。 */}
      {result &&
        ['completed', 'partial', 'blocked', 'aborted'].includes(result.status) &&
        phase !== 'running' &&
        phase !== 'paused' &&
        (!result.dimension_results ||
          Object.keys(result.dimension_results).length === 0) && (
          <div className="bg-[var(--warning-soft)] border border-[var(--warning)] rounded-lg p-3 mb-4 text-sm">
            <div className="font-medium text-[var(--warning)]">该案件的评估结果数据缺失</div>
            <p className="text-muted mt-1">
              后端未返回任何维度结果。可在对应轴点「重跑」，或重新启动评估以恢复本案结论。
            </p>
          </div>
        )}

      {/* 极端情形：本次会话内评估已跑完（phase=done），但最后一次结果刷新失败、
          且 result 仍为空。上方「未开始评估」分支只覆盖 phase=prep，这里补一层。 */}
      {resultError && !result && phase === 'done' && (
        <div className="bg-[var(--danger-soft)] border border-[var(--danger)] rounded-lg p-3 mb-4 text-sm">
          <div className="font-medium text-[var(--danger)]">评估结果加载失败</div>
          <p className="text-muted mt-1">{resultError}</p>
          {onRetryResult && (
            <button
              onClick={onRetryResult}
              className="mt-2 text-xs bg-fg hover:opacity-90 text-canvas rounded px-3 py-1.5"
            >
              重试加载
            </button>
          )}
        </div>
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
              {/* 运行控制条：评估进行中/暂停时挂到轴标题栏右侧（用户要求从页面顶部移入此处）；
                  完成后让位给「查看完整评估结果」。 */}
              {(phase === 'running' || phase === 'paused') && (
                <RunControlBar
                  className="shrink-0"
                  phase={phase === 'paused' ? 'paused' : 'running'}
                  done={runDone}
                  total={runTotal}
                  onPause={onRunPause}
                  onResume={onRunResume}
                  onStop={onRunStop}
                />
              )}
              {result?.scores?.final != null && phase !== 'running' && phase !== 'paused' && (
                <button
                  type="button"
                  onClick={onViewResult}
                  className="shrink-0 text-xs font-medium text-muted hover:text-fg transition-colors"
                >
                  查看完整评估结果 →
                </button>
              )}
            </div>
            {activeGroup.id === 'eval-moot' ? (
              // 模拟法庭（可选）：就地开庭，状态全在父级（切轴不丢场）
              <MootPanel
                mode={moot.mode}
                rounds={moot.shownRounds}
                rawRounds={moot.rounds}
                running={moot.running}
                stopped={moot.stopped}
                judge={moot.judge}
                scoresUpdated={moot.scoresUpdated}
                error={moot.error}
                savedCoeff={mootCoeff}
                canStart={moot.mode === 'standalone' || result?.scores?.final != null}
                canStartHint="完成主诉评估后可启动（红线拦截同样不可启动）"
                onStart={onStartMoot}
                onStop={onStopMoot}
                caseId={caseId}
                pace={moot.pace}
                onPaceChange={onMootPaceChange}
                onTogglePause={onMootTogglePause}
                onStep={onMootStep}
              />
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
