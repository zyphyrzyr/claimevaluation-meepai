import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, CaseItem } from '../api'
import SlideOver from '../components/SlideOver'
import NewCaseForm, { CaseFormInitial } from '../components/NewCaseForm'

const STATUS_LABELS: Record<string, { text: string; dot: string }> = {
  draft: { text: '草稿', dot: 'bg-muted' },
  pending: { text: '待评估', dot: 'bg-warning' },
  evaluating: { text: '评估中', dot: 'bg-info' },
  partial: { text: '部分完成', dot: 'bg-warning' },
  completed: { text: '已完成', dot: 'bg-success' },
  blocked: { text: '红线拦截', dot: 'bg-danger' },
}

function StatusBadge({ status }: { status: string }) {
  const st = STATUS_LABELS[status] ?? STATUS_LABELS.pending
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
      {st.text}
    </span>
  )
}

export default function Workbench() {
  const [cases, setCases] = useState<CaseItem[]>([])
  const [error, setError] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editCaseId, setEditCaseId] = useState<string | null>(null)
  const [editInitial, setEditInitial] = useState<CaseFormInitial | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.listCases().then(setCases).catch((e) => setError(String(e)))
  }, [])

  const openNew = () => {
    setEditCaseId(null)
    setEditInitial(null)
    setDrawerOpen(true)
  }

  const openDraftEdit = (c: CaseItem) => {
    api
      .caseDetail(c.id)
      .then((d: any) => {
        const di = d.context?.defendant_info ?? {}
        setEditInitial({
          name: d.name ?? c.name,
          cause_type: d.cause_type ?? c.cause_type,
          goal_type: d.goal_type ?? c.goal_type,
          client_org: d.client_org ?? '',
          defendant_name: di.name ?? '',
          defendant_type: di.type ?? 'company',
          case_description: d.case_description ?? '',
          evidence_texts: di.evidence_texts ?? '',
          viewpoints: d.context?.user_viewpoints ?? [],
        })
        setEditCaseId(c.id)
        setDrawerOpen(true)
      })
      .catch((e) => setError(String(e)))
  }

  const handleCreated = (id: string, status: string) => {
    setDrawerOpen(false)
    setEditCaseId(null)
    setEditInitial(null)
    api.listCases().then(setCases)
    // 草稿只回工作台；正式建案 / 启动评估跳转到评估页
    if (status !== 'draft') navigate(`/cases/${id}/evaluation`)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-medium">工作台</h1>
        <button
          onClick={openNew}
          className="bg-fg text-canvas hover:opacity-90 text-sm px-4 py-2 rounded-lg transition-colors"
        >
          + 新建案件
        </button>
      </div>

      {error && (
        <div className="bg-danger-soft text-danger text-sm rounded-lg p-4 mb-4">
          后端连接失败：{error}（请确认 uvicorn 已在 8000 端口运行）
        </div>
      )}

      {cases.length === 0 && !error ? (
        <div className="bg-surface border border-line rounded-xl p-16 text-center text-muted">
          还没有案件，点击「新建案件」开始第一次主诉评估
        </div>
      ) : (
        <div className="border border-line rounded-xl overflow-hidden">
          <div className="grid grid-cols-12 gap-4 px-5 py-3 bg-surface text-sm text-muted border-b border-line">
            <div className="col-span-5">案件名称</div>
            <div className="col-span-2">状态</div>
            <div className="col-span-3">案由 / 业务目标</div>
            <div className="col-span-2 text-right">创建时间</div>
          </div>
          {cases.map((c) => {
            const isDraft = c.status === 'draft'
            const className =
              'grid grid-cols-12 gap-4 px-5 py-4 border-b border-line last:border-b-0 hover:bg-surface transition-colors items-center'
            const inner = (
              <>
                <div className="col-span-5 font-medium text-fg">{c.name}</div>
                <div className="col-span-2">
                  <StatusBadge status={c.status} />
                </div>
                <div className="col-span-3 flex gap-2">
                  <span className="text-xs bg-surface text-muted px-2 py-0.5 rounded border border-line">
                    {c.cause_type}
                  </span>
                  <span className="text-xs bg-surface text-muted px-2 py-0.5 rounded border border-line">
                    {c.goal_type}
                  </span>
                </div>
                <div className="col-span-2 text-right text-xs text-muted">
                  {new Date(c.created_at).toLocaleString('zh-CN')}
                </div>
              </>
            )
            if (isDraft) {
              return (
                <div
                  key={c.id}
                  onClick={() => openDraftEdit(c)}
                  className={`${className} cursor-pointer`}
                >
                  {inner}
                </div>
              )
            }
            return (
              <Link
                key={c.id}
                to={c.status === 'pending' ? `/cases/${c.id}/evaluation` : `/cases/${c.id}/dashboard`}
                className={className}
              >
                {inner}
              </Link>
            )
          })}
        </div>
      )}

      {/* 独立模拟法庭快捷入口（三分钟看产品最抓人的部分） */}
      <Link
        to="/moot"
        className="mt-6 flex items-center justify-between bg-surface border border-line rounded-xl p-5 hover:border-fg transition-colors"
      >
        <div>
          <div className="font-medium text-fg">独立模拟法庭 · 诉前对抗演练</div>
          <div className="text-sm text-muted mt-0.5">
            不建案不评估，输入案情直接开庭——AI 被告当庭抗辩，法官归纳薄弱点
          </div>
        </div>
        <span className="text-sm font-medium text-muted hover:text-fg">开庭演练 →</span>
      </Link>

      <SlideOver
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={editCaseId ? '编辑草稿' : '新建案件'}
      >
        <NewCaseForm
          caseId={editCaseId ?? undefined}
          initial={editInitial ?? undefined}
          onCreated={handleCreated}
        />
      </SlideOver>
    </div>
  )
}
