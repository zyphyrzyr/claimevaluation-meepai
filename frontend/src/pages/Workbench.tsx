import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, CaseItem } from '../api'
import SlideOver from '../components/SlideOver'
import NewCaseForm, { FORM_SECTIONS } from '../components/NewCaseForm'
import SectionNav from '../components/SectionNav'

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
  /** 抽屉面板本身：抽屉内表单以它为滚动容器（不是 window） */
  const drawerPanelRef = useRef<HTMLDivElement>(null)
  /** 抽屉内表单的当前步骤（与左侧导航联动） */
  const [drawerStep, setDrawerStep] = useState<string>(FORM_SECTIONS[0].id)
  const navigate = useNavigate()

  useEffect(() => {
    api.listCases().then(setCases).catch((e) => setError(String(e)))
  }, [])

  const openNew = () => {
    setDrawerOpen(true)
  }

  const handleCreated = (id: string, status: string) => {
    setDrawerOpen(false)
    api.listCases().then(setCases)
    // 统一进入个案工作台：草稿在「案件详情」标签补全，其余标签按状态智能选
    navigate(`/cases/${id}`)
  }

  const handleDelete = async (c: CaseItem) => {
    if (!window.confirm(`确认删除案件「${c.name}」？此操作不可恢复，相关评估记录与案件材料库一并清除。`)) {
      return
    }
    try {
      await api.deleteCase(c.id)
      setCases((list) => list.filter((x) => x.id !== c.id))
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-medium">案件列表</h1>
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
          <div className="grid grid-cols-12 gap-3 px-5 py-3 bg-surface text-sm text-muted border-b border-line items-center">
            <div className="col-span-5">案件名称</div>
            <div className="col-span-1">状态</div>
            <div className="col-span-2">案由</div>
            <div className="col-span-1">业务目标</div>
            <div className="col-span-1 text-right">创建时间</div>
            <div className="col-span-2 text-right">操作</div>
          </div>
          {cases.map((c) => {
            const className =
              'grid grid-cols-12 gap-3 px-5 py-4 border-b border-line last:border-b-0 hover:bg-surface transition-colors items-center'
            return (
              <div key={c.id} className={className}>
                <div className="col-span-5 font-medium text-fg min-w-0">
                  <Link to={`/cases/${c.id}`} className="hover:underline line-clamp-2" title={c.name}>{c.name}</Link>
                </div>
                <div className="col-span-1">
                  <StatusBadge status={c.status} />
                </div>
                <div className="col-span-2">
                  <span className="text-xs bg-surface text-muted px-2 py-0.5 rounded border border-line">
                    {c.cause_type}
                  </span>
                </div>
                <div className="col-span-1">
                  <span className="text-xs bg-surface text-muted px-2 py-0.5 rounded border border-line">
                    {c.goal_type}
                  </span>
                </div>
                <div className="col-span-1 text-right text-xs text-muted">
                  {new Date(c.created_at).toLocaleDateString('zh-CN')}
                </div>
                <div className="col-span-2 flex items-center justify-end gap-2">
                  <Link
                    to={`/cases/${c.id}`}
                    className="text-xs text-brand whitespace-nowrap px-2 py-1 rounded border border-brand/30 bg-brand/5 hover:bg-brand/10 transition-colors"
                    title="进入个案工作台"
                  >
                    进入个案工作台
                  </Link>
                  <button
                    onClick={() => handleDelete(c)}
                    className="text-xs text-danger hover:underline whitespace-nowrap"
                  >
                    删除
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <SlideOver
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="新建案件"
        panelRef={drawerPanelRef}
      >
        {/* 抽屉里同样给章节导航留一列（抽屉宽 76rem，减掉 p-6 后 1168px，
            扣掉 144px 导航 + 32px 间距，表单有 992px——「案由/业务目标」两等分后每格 488px，
            分段按钮各 244px，足以让「要钱（判赔规模 · 回款能力）」单行显示不折行）。
            这里滚动容器是抽屉面板自身而非 window，所以粘性偏移用 top-6，不走 25vh。 */}
        <div className="lg:grid lg:grid-cols-[9rem_minmax(0,1fr)] lg:gap-8">
          <div className="hidden lg:block">
            <SectionNav
              sections={FORM_SECTIONS}
              active={drawerStep}
              onSelect={setDrawerStep}
              className="sticky top-6"
            />
          </div>
          <div className="min-w-0">
            <NewCaseForm
              onCreated={handleCreated}
              activeStep={drawerStep}
              onActiveStepChange={setDrawerStep}
              navBreakpoint="lg"
            />
          </div>
        </div>
      </SlideOver>
    </div>
  )
}
