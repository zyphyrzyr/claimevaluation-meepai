import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, runEvaluation, type EvalEvent } from '../api'
import EvalRun, { EVAL_AXES } from './EvalRun'
import DecisionDashboard from './DecisionDashboard'
import NewCaseForm, { FORM_SECTIONS, type CaseFormInitial } from '../components/NewCaseForm'
import SectionNav from '../components/SectionNav'

type Tab = 'detail' | 'run' | 'result'

const TABS: { key: Tab; label: string }[] = [
  { key: 'detail', label: '案件详情' },
  { key: 'run', label: '评估详情' },
  { key: 'result', label: '评估结果' },
]

// 评估已跑过（或正在跑）的状态：不应再显示"尚未开始评估"。
// 注意：红线拦截 blocked 也在此列——它已有结果（硬门禁命中、流程终止），只是 final 为 null。
const EVALUATED_STATUSES = ['evaluating', 'partial', 'completed', 'blocked']

const STATUS_BADGE: Record<string, { text: string; dot: string }> = {
  draft: { text: '草稿', dot: 'bg-muted' },
  pending: { text: '待评估', dot: 'bg-warning' },
  evaluating: { text: '评估中', dot: 'bg-info' },
  partial: { text: '部分完成', dot: 'bg-warning' },
  completed: { text: '已完成', dot: 'bg-success' },
  blocked: { text: '红线拦截', dot: 'bg-danger' },
}

/**
 * 左侧章节导航的纵向定位：两个标签共用同一串类，保证位置完全一致。
 * mt 决定「自然位置」（未滚动时落在哪）＝ main 的 pt-5(20) + mt(25vh+48) = 25vh+68px，
 * 与左侧大导航首项（py-5 + text-xl 行高28 + py-5 + mt-25vh = 25vh+68px）恰好重合；
 * top 决定「粘住后停在哪」，必须等于自然位置，否则滚动到触发点时会跳一段。
 */
const NAV_STICKY = 'mt-[calc(25vh+3rem)] sticky top-[calc(25vh+4.25rem)]'

export default function CaseWorkbench() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<Tab>('detail')
  const [activeStep, setActiveStep] = useState<string>(FORM_SECTIONS[0].id)
  const [defaultChosen, setDefaultChosen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  // 评估运行时状态提升到容器层：确保 SSE 流不会被「标签切换」触发的子组件卸载中断
  const [phase, setPhase] = useState<'prep' | 'running' | 'done'>('prep')
  const [states, setStates] = useState<Record<string, any>>({})
  const [finished, setFinished] = useState('')
  const [injectedInfo, setInjectedInfo] = useState('')
  const [result, setResult] = useState<any>(null)
  const [evalError, setEvalError] = useState('')

  // 评估详情是否已经有可展示的轴：未评估时 EvalRun 只渲染一张「尚未开始评估」卡片，
  // 此时不给左侧导航，避免出现点不动的死链接。（与 EvalRun 的空状态判定同源）
  const evalReady = Boolean(result) || phase !== 'prep'
  // 当前展示的轴（单块逐步）：点击左侧导航 = 切换 activeAxis，与案件详情的 activeStep 同构
  const [activeAxis, setActiveAxis] = useState(EVAL_AXES[0].id)

  const loadDetail = () => {
    if (!id) return
    api
      .caseDetail(id)
      .then((d) => {
        setDetail(d)
        setResult(null)
        setPhase('prep')
        setStates({})
        setFinished('')
        setInjectedInfo('')
        setEvalError('')
      })
      .catch((e) => setError(String(e)))
  }

  useEffect(() => {
    loadDetail()
  }, [id, refreshTick])

  // 首次加载：默认停在「案件详情」（activeTab 初始态已是 detail，不再按 status 跳转到评估页）。
  // 仅负责拉取评估结果：只要状态表明评估已跑过就拉，不再要求 final 有值（红线拦截 case 的 final 为 null）。
  useEffect(() => {
    if (!detail || defaultChosen) return
    setDefaultChosen(true)
    if (id && EVALUATED_STATUSES.includes(detail.status)) {
      api.result(id).then(setResult).catch(() => {})
    }
  }, [detail, defaultChosen, id])

  const refreshResult = () => {
    if (!id) return
    api.result(id).then(setResult).catch(() => {})
  }

  const onEvent = (e: EvalEvent) => {
    if (e.event === 'node_started') {
      setStates((s) => ({ ...s, [e.node]: 'running' }))
    } else if (e.event === 'node_finished') {
      setStates((s) => ({ ...s, [e.node]: (e.status as any) ?? 'ok' }))
      refreshResult()
    } else if (e.event === 'flow_blocked') {
      refreshResult()
    } else if (e.event === 'recall_done') {
      // 后台自动召回完成（方案 B）：材料注入全程后台化，这里只做信息展示
      setInjectedInfo(`${e.label ?? '自动召回完成'}（明细见审计轨迹）`)
    } else if (e.event === 'flow_finished') {
      setFinished(e.status ?? '')
      setPhase('done')
      refreshResult()
      refreshDetailOnly()
    } else if (e.event === 'flow_error') {
      setEvalError(e.error ?? '未知错误')
    }
  }

  // 方案 B：评估启动不再需要手动勾选材料——后端在 run 开头自动召回并留审计。
  // 前端只负责切到评估详情标签并接 SSE 流。
  const startEval = () => {
    if (!id) return
    setEvalError('')
    setInjectedInfo('')
    setStates({})
    setPhase('running')
    setActiveTab('run')
    setDetail((d: any) => (d ? { ...d, status: 'evaluating' } : d))
    refreshResult()
    runEvaluation(id, onEvent).catch((e) => setEvalError(String(e)))
  }

  const rerunNode = async (node: string, guidance: string) => {
    if (!id) return
    try {
      await api.rerun(id, node, guidance)
      await refreshResult()
    } catch (e) {
      setEvalError(String(e))
    }
  }

  // 仅刷新 detail，不复位运行时状态（评估结束后同步案件状态徽标用）
  const refreshDetailOnly = () => {
    if (!id) return
    api.caseDetail(id).then(setDetail).catch(() => {})
  }

  // 草稿在「案件详情」标签保存后，重新拉取并复位默认标签选择
  const onDraftSaved = () => {
    setDefaultChosen(false)
    setRefreshTick((t) => t + 1)
  }
  // 「保存并启动评估」：落库成功后直接进入评估运行（方案 B：无准备页，后台自动召回材料）
  const onDraftStarted = () => {
    startEval()
  }

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!detail) return <div className="text-muted text-sm">加载中…</div>

  const status = detail.status
  const st = STATUS_BADGE[status] ?? STATUS_BADGE.pending
  const evaluated = EVALUATED_STATUSES.includes(status)

  const fileCount = detail.evidence_files?.length ?? 0

  return (
    <div className="max-w-[67rem] mx-auto xl:grid xl:grid-cols-[9rem_minmax(0,1fr)] xl:gap-8">
      {/* 章节导航列：正好填进左侧边栏与内容区之间原先空着的那段留白。
          两个标签共用这一列，纵向定位也共用 NAV_STICKY，所以位置完全一致。

          粘性与偏移都挂在内层 SectionNav 上（本列只是占位容器，不参与定位）——
          注意不能挂到这个网格项上：网格项会被拉伸到整行高度，自身没有可粘行程，
          sticky 会退化成「一开始就贴在网格顶部」，导航直接跳到页面上方。
          实测（headless Chrome 1440x757，侧边栏首项 y=257）：
          mt/top 同值时 scroll≥20 后差值为 -20（往上蹿一段）；让 top 等于自然位置后全程差值 0。

          两种驱动模式共用同一个受控组件，只是喂进去的 active / onSelect 不同：
          · 案件详情：单块逐步表单 → active=activeStep，点击=切换显示哪一块（不滚动）
          · 评估详情：单块逐步（切轴） → active=activeAxis，点击=切换展示哪一个轴（不滚动）
          两者都用 STEP_MOTION 做切换动画，交互完全一致；左侧列只负责定位与高亮。
          评估结果标签暂无章节结构，保留空列占位，避免切换标签时内容横向位移。 */}
      <div className="hidden xl:block">
        {activeTab === 'detail' && (
          <SectionNav
            sections={FORM_SECTIONS}
            active={activeStep}
            onSelect={setActiveStep}
            className={NAV_STICKY}
          />
        )}
        {activeTab === 'run' && evalReady && (
          <SectionNav
            sections={EVAL_AXES}
            active={activeAxis}
            onSelect={setActiveAxis}
            className={NAV_STICKY}
          />
        )}
      </div>

      <div className="min-w-0">
        {/* 面包屑：把「这是个案件工作台」降级为导航信息，不再占用页面标题 */}
        <nav className="flex items-center gap-2 text-xs text-muted mb-3">
          <button onClick={() => navigate('/workbench')} className="hover:text-fg transition-colors">
            案件列表
          </button>
          <span className="text-line">/</span>
          <span>案件工作台</span>
        </nav>

        {/* 主标题就是案件名：完整展示、可换行，不再截断 */}
        <div className="flex items-start gap-3">
          <h1 className="flex-1 min-w-0 text-2xl font-medium leading-snug break-words">{detail.name}</h1>
          <span className="mt-1.5 inline-flex items-center gap-1.5 shrink-0 rounded-full border border-line px-2.5 py-1 text-xs text-muted">
            <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
            {st.text}
          </span>
        </div>

        {/* 摘要带：把关键事实前置，进页面即可确认是哪个案子、什么目标、证据齐不齐 */}
        <div className="flex flex-wrap gap-2 mt-3">
          <SummaryChip label="案由" value={detail.cause_type} />
          <SummaryChip label="业务目标" value={detail.goal_type} />
          <SummaryChip label="证据" value={fileCount > 0 ? `${fileCount} 份文件` : '未上传'} />
        </div>

        {/* 标签导航：独立成行 + 下划线指示（原先只靠字重，选中态几乎看不出来） */}
        <div className="flex items-center gap-6 mt-6 border-b border-line">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`relative pb-3 text-sm transition-colors ${
                activeTab === t.key ? 'text-fg font-medium' : 'text-muted hover:text-fg'
              }`}
            >
              {t.label}
              {activeTab === t.key && (
                <span className="absolute -bottom-px left-0 right-0 h-[2px] rounded-full bg-fg" />
              )}
            </button>
          ))}
        </div>

        <div className="mt-6">
          {activeTab === 'detail' && (
            <CaseDetailTab
              detail={detail}
              evaluated={evaluated}
              activeStep={activeStep}
              onActiveStepChange={setActiveStep}
              onSaved={onDraftSaved}
              onStarted={onDraftStarted}
              onStartMoot={(cid) => navigate(`/cases/${cid}/moot?mode=standalone`)}
            />
          )}
          {activeTab === 'run' && (
            <EvalRun
              caseId={id!}
              result={result}
              states={states}
              phase={phase}
              finished={finished}
              error={evalError}
              activeAxis={activeAxis}
              onAxisChange={setActiveAxis}
              onStart={startEval}
              onViewResult={() => setActiveTab('result')}
              onRerun={rerunNode}
            />
          )}
          {activeTab === 'result' && <DecisionDashboard />}
        </div>

        {injectedInfo && (
          <div className="bg-[var(--info-soft)] text-[var(--info)] rounded-lg px-4 py-2 text-xs mt-4">
            {injectedInfo}
          </div>
        )}
      </div>
    </div>
  )
}

/** 摘要带上的单个事实标签（无值时不渲染，避免出现「业务目标：—」这类空壳） */
function SummaryChip({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs">
      <span className="text-muted">{label}</span>
      <span className="text-fg">{value}</span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// 案件详情标签：始终为可编辑表单（与新建案件页共用 NewCaseForm，布局同步加分）
// evaluated 时把「建议重新评估」提示交给表单吸底操作条，不再用满宽色块压住页首
// ---------------------------------------------------------------------------
function CaseDetailTab({
  detail,
  evaluated,
  activeStep,
  onActiveStepChange,
  onSaved,
  onStarted,
  onStartMoot,
}: {
  detail: any
  evaluated: boolean
  activeStep: string
  onActiveStepChange: (id: string) => void
  onSaved: () => void
  onStarted: () => void
  onStartMoot: (caseId: string) => void
}) {
  const initial: CaseFormInitial = {
    name: detail.name ?? '',
    cause_type: detail.cause_type ?? '商标侵权',
    goal_type: detail.goal_type ?? '要钱',
    client_org: detail.client_org ?? '',
    defendant_name: detail.context?.defendant_info?.name ?? '',
    defendant_type: detail.context?.defendant_info?.type ?? 'company',
    case_description: detail.case_description ?? '',
    evidence_texts: detail.context?.defendant_info?.evidence_texts ?? '',
    viewpoints: detail.context?.user_viewpoints ?? [],
    evidence_files: detail.evidence_files ?? [],
  }
  return (
    <NewCaseForm
      caseId={detail.id}
      initial={initial}
      activeStep={activeStep}
      onActiveStepChange={onActiveStepChange}
      notice={evaluated ? '已有评估结果，修改后建议重新评估' : undefined}
      onCreated={(cid, status) => {
        if (status === 'pending') onStarted()
        else onSaved()
      }}
      onStartMoot={onStartMoot}
    />
  )
}

