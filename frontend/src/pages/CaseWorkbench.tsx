import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api, knowledgeApi, runEvaluation, type EvalEvent } from '../api'
import { cn } from '../lib/utils'
import EvalPrep from './EvalPrep'
import EvalRun from './EvalRun'
import DecisionDashboard from './DecisionDashboard'
import NewCaseForm, { type CaseFormInitial } from '../components/NewCaseForm'
import { Card } from '../components/ui/Card'
import Tooltip from '../components/ui/Tooltip'

type Tab = 'detail' | 'prep' | 'run' | 'result'

const TABS: { key: Tab; label: string }[] = [
  { key: 'detail', label: '案件详情' },
  { key: 'prep', label: '评估准备' },
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

export default function CaseWorkbench() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<Tab>('detail')
  const [defaultChosen, setDefaultChosen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  // 评估运行时状态提升到容器层：确保 SSE 流不会被「标签切换」触发的子组件卸载中断
  const [phase, setPhase] = useState<'prep' | 'running' | 'done'>('prep')
  const [states, setStates] = useState<Record<string, any>>({})
  const [finished, setFinished] = useState('')
  const [injectedInfo, setInjectedInfo] = useState('')
  const [result, setResult] = useState<any>(null)
  const [evalError, setEvalError] = useState('')

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
    } else if (e.event === 'flow_finished') {
      setFinished(e.status ?? '')
      setPhase('done')
      refreshResult()
    } else if (e.event === 'flow_error') {
      setEvalError(e.error ?? '未知错误')
    }
  }

  const startEval = async (selectedIds: string[]) => {
    if (!id) return
    setEvalError('')
    setInjectedInfo('')
    try {
      if (selectedIds.length > 0) {
        const r = await knowledgeApi.inject(id, selectedIds)
        setInjectedInfo(`已注入 ${r.injected} 条参考材料到全部评估节点`)
      } else {
        await knowledgeApi.inject(id, [])
      }
    } catch (e) {
      setEvalError(`材料注入失败：${e}`)
      return
    }
    setStates({})
    setPhase('running')
    setActiveTab('run')
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

  // 草稿在「案件详情」标签保存/启动后，重新拉取并复位默认标签选择
  const onDraftSaved = () => {
    setDefaultChosen(false)
    setRefreshTick((t) => t + 1)
  }
  const onDraftStarted = () => {
    setDefaultChosen(false)
    setActiveTab('prep')
    setRefreshTick((t) => t + 1)
  }

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!detail) return <div className="text-muted text-sm">加载中…</div>

  const status = detail.status
  const st = STATUS_BADGE[status] ?? STATUS_BADGE.pending
  const evaluated = EVALUATED_STATUSES.includes(status)

  return (
    <div>
      {/* 顶部区域：返回 / 标题 / 案件名（降级副说明） / 状态 / 标签导航 */}
      <div className="mb-6">
        <div className="flex items-center flex-wrap gap-x-3 gap-y-2">
          <button
            onClick={() => navigate('/workbench')}
            className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg transition-colors shrink-0"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            案件列表
          </button>

          <h1 className="text-xl font-medium shrink-0">个案工作台</h1>

          <span className="text-muted shrink-0">·</span>

          <Tooltip content={detail.name}>
            <span className="inline-block max-w-xs text-sm text-muted truncate align-middle">
              {detail.name}
            </span>
          </Tooltip>

          <span className="inline-flex items-center gap-1.5 text-xs text-muted shrink-0 rounded-full border border-line px-2 py-0.5">
            <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
            {st.text}
          </span>

          <nav className="flex items-center gap-1 flex-wrap ml-auto">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                className={`px-3 py-2 rounded-md text-sm transition-colors ${
                  activeTab === t.key ? 'text-fg font-medium' : 'text-muted hover:text-fg'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
      </div>

      {activeTab === 'detail' && (
        <CaseDetailTab
          detail={detail}
          evaluated={evaluated}
          onSaved={onDraftSaved}
          onStarted={onDraftStarted}
          onStartMoot={(cid) => navigate(`/cases/${cid}/moot?mode=standalone`)}
        />
      )}
      {activeTab === 'prep' && (
        <EvalPrep caseId={id!} caseDetail={detail} evaluated={evaluated} onStart={startEval} error={evalError} />
      )}
      {activeTab === 'run' && (
        <EvalRun
          caseId={id!}
          result={result}
          states={states}
          phase={phase}
          finished={finished}
          error={evalError}
          onViewResult={() => setActiveTab('result')}
          onRerun={rerunNode}
        />
      )}
      {activeTab === 'result' && <DecisionDashboard />}

      {injectedInfo && (
        <div className="bg-[var(--info-soft)] text-[var(--info)] rounded-lg px-4 py-2 text-xs mt-4">
          {injectedInfo}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 案件详情标签：始终为可编辑表单（与新建案件页布局一致）
// 已评估案件（evaluated）顶部显示提示，引导重新评估；未评估不提示
// ---------------------------------------------------------------------------
function CaseDetailTab({
  detail,
  evaluated,
  onSaved,
  onStarted,
  onStartMoot,
}: {
  detail: any
  evaluated: boolean
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
    <>
      {evaluated && (
        <div className="bg-[var(--warning-soft)] text-[var(--warning)] border border-[var(--warning)]/30 rounded-lg px-4 py-2.5 text-sm mb-4">
          已有评估结果，修改后建议重新评估
        </div>
      )}
      <NewCaseForm
        caseId={detail.id}
        initial={initial}
        onCreated={(cid, status) => {
          if (status === 'pending') onStarted()
          else onSaved()
        }}
        onStartMoot={onStartMoot}
      />
    </>
  )
}

// 建案时录入的全部字段回看
function CaseInfoCard({
  detail,
  descExpanded,
  setDescExpanded,
}: {
  detail: any
  descExpanded: boolean
  setDescExpanded: Dispatch<SetStateAction<boolean>>
}) {
  const di = detail.context?.defendant_info ?? {}
  const views: string[] = detail.context?.user_viewpoints ?? []
  const files: { id: string; file_name: string; parse_status: string }[] = detail.evidence_files ?? []
  const desc = detail.case_description ?? ''
  const evText = di.evidence_texts ?? ''
  return (
    <Card className="p-5">
      <h2 className="text-sm font-medium mb-3">案件信息</h2>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-3 text-sm">
        <div>
          <div className="text-xs text-muted mb-0.5">案件名称</div>
          <div className="text-fg">{detail.name || '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted mb-0.5">业务目标</div>
          <div className="text-fg">{detail.goal_type || '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted mb-0.5">案由</div>
          <div className="text-fg">{detail.cause_type || '—'}</div>
        </div>
        <div>
          <div className="text-xs text-muted mb-0.5">原告 / 客户主体</div>
          <div className="text-fg">{detail.client_org || '—'}</div>
        </div>
        <div className="sm:col-span-2">
          <div className="text-xs text-muted mb-0.5">被告</div>
          <div className="text-fg">
            {di.name || '—'}
            {di.type ? `（${di.type === 'company' ? '企业' : di.type === 'individual' ? '个人' : di.type}）` : ''}
          </div>
        </div>
        <div className="sm:col-span-2">
          <div className="text-xs text-muted mb-0.5">案情描述</div>
          <div className={cn('text-fg whitespace-pre-wrap max-w-3xl', !descExpanded && 'line-clamp-3')}>
            {desc || '—'}
          </div>
          {desc.length > 120 && (
            <button
              onClick={() => setDescExpanded((v) => !v)}
              className="text-xs text-brand hover:underline mt-1"
            >
              {descExpanded ? '收起' : '展开全文'}
            </button>
          )}
        </div>
      </div>

      {evText && (
        <div className="mt-3">
          <div className="text-xs text-muted mb-1">证据材料文本</div>
          <div className="text-xs text-fg whitespace-pre-wrap bg-canvas border border-line rounded-lg p-3 max-h-40 overflow-auto max-w-3xl">
            {evText}
          </div>
        </div>
      )}

      {files.length > 0 && (
        <div className="mt-3">
          <div className="text-xs text-muted mb-1">已上传证据文件（{files.length}）</div>
          <div className="flex flex-wrap gap-2">
            {files.map((f) => (
              <span key={f.id} className="text-xs bg-canvas border border-line rounded px-2 py-1 text-fg">
                {f.file_name}
                {f.parse_status === 'ok' ? ' · 已解析' : f.parse_status === 'failed' ? ' · 解析失败' : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      {views.length > 0 && (
        <div className="mt-3">
          <div className="text-xs text-muted mb-1">已注入观点</div>
          {views.map((v: string, i: number) => (
            <div key={i} className="text-sm text-muted">· {v}</div>
          ))}
        </div>
      )}
    </Card>
  )
}
