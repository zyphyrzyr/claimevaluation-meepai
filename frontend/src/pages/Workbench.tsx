import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, CaseItem } from '../api'
import SlideOver from '../components/SlideOver'
import NewCaseForm, { FORM_SECTIONS } from '../components/NewCaseForm'
import SectionNav from '../components/SectionNav'

/**
 * 案件列表。
 *
 * 这一页此前是全量渲染 + 无限往下滑。全库已有 279 个案件，那个做法的问题不是「慢」，
 * 而是**没有位置感**：滚到第 150 行时既不知道自己在哪，也回不去上次看的地方。
 * 所以改成服务端分页 + 关键词搜索。
 *
 * 搜索必须走后端：当事人存在 parties 表，不在案件名称里。案件名称里**不一定**
 * 写着当事人（全库名含「诉」字的案件只有个位数）——纯前端过滤永远搜不到「顾家家居」。
 */

const STATUS_LABELS: Record<string, { text: string; dot: string }> = {
  draft: { text: '草稿', dot: 'bg-muted' },
  pending: { text: '待评估', dot: 'bg-warning' },
  evaluating: { text: '评估中', dot: 'bg-info' },
  partial: { text: '部分完成', dot: 'bg-warning' },
  completed: { text: '已完成', dot: 'bg-success' },
  blocked: { text: '红线拦截', dot: 'bg-danger' },
}

const PAGE_SIZE_OPTIONS = [10, 20, 50]

/** 输入停顿多久才真正去搜——太短会边打字边发请求，太长会显得迟钝 */
const SEARCH_DEBOUNCE_MS = 300

const GRID = 'grid grid-cols-12 gap-3 px-5 items-center'

function StatusBadge({ status }: { status: string }) {
  const st = STATUS_LABELS[status] ?? STATUS_LABELS.pending
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted">
      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
      {st.text}
    </span>
  )
}

/**
 * 当事人一行灰字：搜「顾家家居」命中时，用户得看得出是匹配在哪。
 *
 * 有数据才显示，两边都没有就整行不占位（早期草稿与 E2E 测试案常没登记当事人）。
 */
export function partyLine(c: Pick<CaseItem, 'plaintiff' | 'defendant'>): string {
  const p = (c.plaintiff ?? '').trim()
  const d = (c.defendant ?? '').trim()
  if (p && d) return `${p} 诉 ${d}`
  if (p) return `原告：${p}`
  if (d) return `被告：${d}`
  return ''
}

/**
 * 页码窗口：总页数多时只显示首页、尾页和当前页附近的，中间折成省略号。
 *
 * 导出来单独放，是为了能直接断言——这是「第几页」唯一的计算处，
 * 算错的话症状是点页码跳到别处，而不是报错。
 */
export function pageWindow(current: number, totalPages: number): (number | '...')[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  const out: (number | '...')[] = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(totalPages - 1, current + 1)
  if (start > 2) out.push('...')
  for (let i = start; i <= end; i++) out.push(i)
  if (end < totalPages - 1) out.push('...')
  out.push(totalPages)
  return out
}

function PageBtn({
  disabled, onClick, children,
}: { disabled: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="px-2.5 py-1 rounded-md text-xs text-muted hover:text-fg transition-colors disabled:opacity-30 disabled:hover:text-muted"
    >
      {children}
    </button>
  )
}

export default function Workbench() {
  const [items, setItems] = useState<CaseItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE_OPTIONS[0])
  /** 输入框里正在敲的字 */
  const [q, setQ] = useState('')
  /** 已经生效、真正发给后端的关键词（输入框 debounce 之后同步过来） */
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  /** 删除后手动触发重取，避免把「重取」伪装成某个状态变化 */
  const [reloadKey, setReloadKey] = useState(0)
  const [error, setError] = useState('')

  const [drawerOpen, setDrawerOpen] = useState(false)
  /** 抽屉面板本身：抽屉内表单以它为滚动容器（不是 window） */
  const drawerPanelRef = useRef<HTMLDivElement>(null)
  /** 抽屉内表单的当前步骤（与左侧导航联动） */
  const [drawerStep, setDrawerStep] = useState<string>(FORM_SECTIONS[0].id)
  const navigate = useNavigate()

  // 首次渲染不必 debounce（没有「刚敲的字」要等），否则会白等 300ms 才出数据
  const mounted = useRef(false)
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return }
    const t = setTimeout(() => {
      setQuery(q)
      // 换了关键词必须回到第 1 页：停在第 5 页去搜一个新词，多半会搜出空页面，
      // 而用户会以为「没搜到」而不是「页码越界了」
      setPage(1)
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.listCases({ q: query, page, page_size: pageSize })
      .then((res) => {
        if (!alive) return
        setItems(res.items)
        setTotal(res.total)
        setError('')
        // 后端会把越界页码夹到最后一页，这里跟随它——删空最后一页时自动退一页
        if (res.page !== page) setPage(res.page)
      })
      .catch((e) => { if (alive) setError(String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [query, page, pageSize, reloadKey])

  const openNew = () => {
    setDrawerOpen(true)
  }

  const handleCreated = (id: string) => {
    setDrawerOpen(false)
    // 建完直接进个案工作台，不必再拉一次列表——这次拉取的结果当场就被路由切换丢弃了
    navigate(`/cases/${id}`)
  }

  const handleDelete = async (c: CaseItem) => {
    if (!window.confirm(`确认删除案件「${c.name}」？此操作不可恢复，相关评估记录与案件材料库一并清除。`)) {
      return
    }
    try {
      await api.deleteCase(c.id)
      setReloadKey((k) => k + 1)
    } catch (e) {
      setError(String(e))
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const isSearching = query.length > 0
  const window_ = pageWindow(page, totalPages)

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

      {/* 工具条：搜索是全库搜，不是只过滤当前页 */}
      <div className="flex items-center gap-3 mb-3">
        <div className="relative w-full max-w-md">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索案件名称、原告或被告，如「栖木 顾家」"
            className="w-full border border-line rounded-lg pl-3 pr-8 py-2 text-sm focus:outline-none focus:border-brand"
          />
          {q && (
            <button
              onClick={() => setQ('')}
              aria-label="清除搜索"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-fg text-base leading-none"
            >
              ×
            </button>
          )}
        </div>
        <span className="text-xs text-muted shrink-0">
          {isSearching ? `匹配 ${total} 个案件` : `共 ${total} 个案件`}
        </span>
        {loading && <span className="text-xs text-muted shrink-0">加载中…</span>}
      </div>

      {items.length === 0 ? (
        <div className="border border-line rounded-xl p-16 text-center text-muted text-sm">
          {isSearching ? (
            <>
              没有匹配「{query}」的案件。
              <button onClick={() => setQ('')} className="text-brand hover:underline ml-1">
                清除搜索
              </button>
            </>
          ) : loading ? (
            '加载中…'
          ) : (
            '还没有案件，点击「新建案件」开始第一次主诉评估'
          )}
        </div>
      ) : (
        <>
          {/* 不加 overflow-hidden：那会让 sticky 表头认这个容器当滚动祖先，粘不住 */}
          <div className="border border-line rounded-xl">
            <div className={`${GRID} py-3 bg-surface text-sm text-muted border-b border-line rounded-t-xl sticky top-0 z-10`}>
              <div className="col-span-5">案件名称</div>
              <div className="col-span-1">状态</div>
              <div className="col-span-2">案由</div>
              <div className="col-span-1">业务目标</div>
              <div className="col-span-1 text-right">创建时间</div>
              <div className="col-span-2 text-right">操作</div>
            </div>
            {items.map((c) => {
              const parties = partyLine(c)
              return (
                <div
                  key={c.id}
                  className={`${GRID} py-4 border-b border-line last:border-b-0 hover:bg-surface transition-colors`}
                >
                  <div className="col-span-5 min-w-0">
                    <Link
                      to={`/cases/${c.id}`}
                      className="font-medium text-fg hover:underline line-clamp-2"
                      title={c.name}
                    >
                      {c.name}
                    </Link>
                    {parties && (
                      <div className="text-[11px] text-muted mt-0.5 truncate" title={parties}>
                        {parties}
                      </div>
                    )}
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
                  <div className="col-span-2 flex items-center justify-end gap-3">
                    {/* 纯黑文字：入口仍在，但不再是一个抢注意力的彩色描边按钮 */}
                    <Link
                      to={`/cases/${c.id}`}
                      className="text-xs text-fg hover:underline whitespace-nowrap"
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

          <div className="flex items-center justify-between gap-3 mt-3 flex-wrap">
            <span className="text-xs text-muted">
              第 {page} / {totalPages} 页 · 共 {total} 条
            </span>
            <div className="flex items-center gap-1">
              <PageBtn disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</PageBtn>
              {window_.map((p, i) =>
                p === '...' ? (
                  <span key={`gap-${i}`} className="px-1 text-xs text-muted">…</span>
                ) : (
                  <button
                    key={p}
                    onClick={() => setPage(p)}
                    className={`min-w-[2rem] px-2 py-1 rounded-md text-xs transition-colors ${
                      p === page
                        ? 'text-fg font-medium bg-surface border border-line'
                        : 'text-muted hover:text-fg'
                    }`}
                  >
                    {p}
                  </button>
                ),
              )}
              <PageBtn disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</PageBtn>

              <select
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}
                className="ml-2 border border-line rounded-md text-xs text-muted px-2 py-1 bg-canvas focus:outline-none focus:border-brand"
                aria-label="每页条数"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>每页 {n} 条</option>
                ))}
              </select>
            </div>
          </div>
        </>
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
