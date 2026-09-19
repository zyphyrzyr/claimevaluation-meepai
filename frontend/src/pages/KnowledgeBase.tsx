import { useEffect, useMemo, useState } from 'react'
import { KnowledgeEntryItem, SearchHit, humanError, knowledgeApi } from '../api'
import SlideOver from '../components/SlideOver'
import { useAuth } from '../auth/AuthProvider'
import { fmtDateTime, relativeTime } from '../lib/time'
import { markdownToPlainText } from '../lib/utils'

/**
 * 个人知识库（§7 RAG 双集合分库之一；界面上原叫「全局经验库」）
 * 来源 B 手动录入 / C 观点沉淀 / D 独立建库；案件材料库在各案件评估准备页自动入库
 *
 * 版式：列表是主体，检索是列表的控制器，新增收进抽屉。
 *
 * 早先这里是「左卡新增 + 右卡检索 + 下方列表」三块并列，那套排法来自 P3 阶段——
 * 当时的目标是**证明召回通道能跑通**，所以版式按「两个并列的演示单元」来排。
 * 现在通道早就是系统关键路径（评估启动即自动召回），这一页的真实职责变成了
 * 「管理一批资料」，三块并列就暴露出两个问题：
 *   1. 检索结果和条目列表本来就是同一批东西的两个视图，拆成两张卡会互相顶位置——
 *      命中一多，下面的列表就被顶下去；
 *   2. 录入是低频动作（一次办案沉淀一两条），却常年占掉半屏。
 * 所以检索并入工具条、新增收进抽屉，整页只留一个列表。
 */

const SOURCE_BADGE: Record<string, string> = {
  B: 'bg-[var(--info-soft)] text-[var(--info)]',
  C: 'bg-[var(--brand-2-soft)] text-[var(--brand-2)]',
  D: 'bg-[var(--brand-soft)] text-[var(--brand)]',
}

// 筛选条上按来源统计的顺序即此；A（证据文档）永远是 scope=case，不会出现在本页
const SOURCE_LABEL: Record<string, string> = {
  B: '手动录入',
  C: '观点沉淀',
  D: '独立建库',
}

/**
 * 观点沉淀（C）存的是评估结果 markdown 原文，展示时转成干净纯文本；
 * 其余来源（B/D 手动录入、A 证据）本就是纯文本，原样显示，不做任何处理。
 */
const displayText = (sourceType: string | undefined, text: string) =>
  sourceType === 'C' ? markdownToPlainText(text) : (text ?? '')

export default function KnowledgeBase() {
  const auth = useAuth()
  const [entries, setEntries] = useState<KnowledgeEntryItem[]>([])
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [sourceFilter, setSourceFilter] = useState('all')
  const [expandId, setExpandId] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  // 新增抽屉
  const [addOpen, setAddOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)

  // 文件导入（单文件预览 + 批量）
  const [importBusy, setImportBusy] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchFiles, setBatchFiles] = useState<File[]>([])
  const [batchBusy, setBatchBusy] = useState(false)
  const [batchResult, setBatchResult] = useState<{
    ok: number
    failed: number
    results: { filename: string; ok: boolean; error?: string; title?: string }[]
  } | null>(null)

  const load = () => {
    knowledgeApi.entries({ scope: 'global' })
      .then(setEntries)
      .catch((e) => setError(humanError(e)))
  }

  // 换账号要重拉：可见范围是「自己的 + 公共的」，换人就换了结果集。
  // 少了这一项，登出后屏幕上还挂着上一个人的私有经验。
  const ownerKey = auth.user?.id ?? 'anonymous'
  useEffect(() => {
    setHits(null)
    load()
  }, [ownerKey])

  /**
   * 当前展示的「底表」：检索态用命中，否则用全部条目。
   *
   * 来源筛选与计数都基于它，所以两个态下工具条的含义是一致的
   * （「全部 27」说的就是屏幕上这一份有 27 条）。
   */
  const base: (KnowledgeEntryItem | SearchHit)[] = hits ?? entries
  const isSearch = hits !== null

  const counts = useMemo(() => {
    const m: Record<string, number> = {}
    for (const e of base) m[e.source_type] = (m[e.source_type] ?? 0) + 1
    return m
  }, [base])

  const shown = sourceFilter === 'all'
    ? base
    : base.filter((e) => e.source_type === sourceFilter)

  const add = async () => {
    if (!title.trim() || !content.trim()) return
    setSaving(true)
    setError('')
    try {
      await knowledgeApi.createEntry({
        scope: 'global', source_type: 'B',
        title: title.trim(), content: content.trim(),
      })
      setTitle('')
      setContent('')
      setAddOpen(false)
      setNotice('已入个人知识库，评估启动时会参与自动召回。')
      auth.refresh()
      load()
    } catch (e) {
      setError(humanError(e))
    } finally {
      setSaving(false)
    }
  }

  /**
   * 单文件导入：抽取文本后填进「新增经验」抽屉的内容框，标题留空时自动填文件名
   * （去扩展名）。内容框已有文字则保留，不覆盖用户的手动输入。
   */
  const importToEditor = async (file: File) => {
    setImportBusy(true)
    setError('')
    try {
      const res = await knowledgeApi.uploadFile(file)
      setTitle((prev) => prev.trim() || (file.name.replace(/\.[^.]+$/, '') || '未命名文件'))
      setContent((prev) => (prev.trim() ? prev : res.text))
      setNotice(`已从「${res.filename}」导入 ${res.chars.toLocaleString()} 字，可校对后再入库。`)
    } catch (e) {
      setError(humanError(e))
    } finally {
      setImportBusy(false)
    }
  }

  const startBatch = async () => {
    if (!batchFiles.length) return
    setBatchBusy(true)
    setError('')
    setBatchResult(null)
    try {
      const res = await knowledgeApi.importFiles(batchFiles)
      setBatchResult(res)
      setBatchFiles([])
      auth.refresh()
      load()
    } catch (e) {
      setError(humanError(e))
    } finally {
      setBatchBusy(false)
    }
  }

  const remove = async (id: string, name: string) => {
    if (!confirm(`删除知识库条目「${name}」？向量将同步清除。`)) return
    try {
      await knowledgeApi.deleteEntry(id)
      // 删掉的条目可能正在命中结果里，一起清掉免得留下点不动的幽灵行
      setHits((prev) => (prev ? prev.filter((h) => h.id !== id) : prev))
      setExpandId('')
      auth.refresh()
      load()
    } catch (e) {
      setError(humanError(e))
    }
  }

  const search = async () => {
    if (!query.trim()) return
    setSearching(true)
    setHits(null)
    setError('')
    setExpandId('')
    try {
      setHits(await knowledgeApi.search({ query: query.trim(), scope: 'global', top_k: 5 }))
    } catch (e) {
      setError(humanError(e))
    } finally {
      setSearching(false)
    }
  }

  const clearSearch = () => {
    setQuery('')
    setHits(null)
    setExpandId('')
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        {/* 标题只留一行：下面的副标题原本在讲「跨案件沉淀…共用同一召回通道」，
            末尾还挂着向量后端与 embedding 型号——那是给开发看的实现细节，
            摆在客户面前既占地方又漏了技术栈，整段撤掉。 */}
        <h1 className="text-xl font-medium">个人知识库</h1>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => { setAddOpen(true); setNotice('') }}
            className="bg-brand hover:bg-fg text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            + 新增经验
          </button>
          <button
            onClick={() => { setBatchOpen(true); setBatchResult(null) }}
            className="border border-line hover:border-brand text-fg px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            批量导入
          </button>
        </div>
      </div>

      {error && <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm">{error}</div>}
      {notice && <div className="bg-[var(--success-soft)] text-[var(--success)] rounded-lg p-3 text-sm">{notice}</div>}

      <div className="bg-surface rounded-xl border border-line p-5">
        {/* 工具条：检索 + 来源筛选，都作用在下面这一个列表上 */}
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="语义检索本库，如：杭州 商标 判赔水平"
            className="flex-1 min-w-0 border border-line rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-brand"
          />
          <button
            onClick={search}
            disabled={searching || !query.trim()}
            className="bg-fg text-white px-4 rounded-lg text-sm disabled:opacity-40 transition-colors shrink-0"
          >
            {searching ? '检索中' : '检索'}
          </button>
        </div>

        {isSearch && (
          <div className="flex items-center gap-2 mt-2.5 text-xs">
            <span className="text-[var(--info)] bg-[var(--info-soft)] rounded-full px-2.5 py-0.5">
              命中 {hits!.length} 条
            </span>
            <span className="text-muted">与评估召回同一通道</span>
            <button onClick={clearSearch} className="ml-auto text-muted hover:text-brand">
              清除检索 ×
            </button>
          </div>
        )}

        <div className="flex items-center gap-1 flex-wrap mt-3.5 pt-3 border-t border-line text-xs">
          <FilterChip active={sourceFilter === 'all'} onClick={() => setSourceFilter('all')}>
            全部 {base.length}
          </FilterChip>
          {Object.entries(SOURCE_LABEL).map(([key, label]) =>
            counts[key] ? (
              <FilterChip key={key} active={sourceFilter === key} onClick={() => setSourceFilter(key)}>
                {label} {counts[key]}
              </FilterChip>
            ) : null,
          )}
        </div>

        {/* 列表：检索态与全量态共用同一套行 */}
        {shown.length ? (
          <div className="mt-1">
            {shown.map((e) => {
              const hit = 'score' in e ? (e as SearchHit) : null
              const open = expandId === e.id
              return (
                <div key={e.id} className="border-b border-line last:border-b-0">
                  <div className="flex items-center gap-2.5 pt-3">
                    <span
                      className={`text-[11px] px-2 py-0.5 rounded-full shrink-0 ${SOURCE_BADGE[e.source_type] ?? 'bg-surface text-muted'}`}
                    >
                      {e.source_type_label}
                    </span>
                    <button
                      onClick={() => setExpandId(open ? '' : e.id)}
                      className="text-sm font-medium hover:text-brand text-left min-w-0 truncate"
                      title={open ? '收起' : '展开全文'}
                    >
                      {e.title}
                    </button>
                    <span className="ml-auto text-xs text-muted shrink-0 whitespace-nowrap">
                      {relativeTime(e.created_at)}
                    </span>
                    {hit && (
                      <span className="text-xs text-[var(--info)] bg-[var(--info-soft)] rounded-full px-2 py-0.5 shrink-0">
                        {Math.round(hit.score * 100)}%
                      </span>
                    )}
                  </div>

                  {/* 摘要：检索态显示真正命中的那一段，全量态显示内容开头 */}
                  <p className="text-xs text-muted mt-1 truncate">
                    {hit
                      ? displayText(hit.source_type, hit.matched_chunk)
                      : displayText(e.source_type, e.snippet ?? e.content ?? '')}
                  </p>

                  {open && (
                    <div className="mt-2 mb-1 bg-canvas rounded-lg p-3">
                      <div className="text-xs text-muted whitespace-pre-wrap leading-relaxed">{displayText(e.source_type, e.content ?? '')}</div>
                      <div className="flex items-center gap-4 flex-wrap mt-3 pt-2 border-t border-line text-[11px] text-muted">
                        <span>来源 {e.source_type_label}</span>
                        <span>入库 {fmtDateTime(e.created_at)}</span>
                        <span>分块 {e.chunk_count} 块</span>
                        {e.is_public ? (
                          // 公共经验谁都删不掉（后端 403）。给了按钮再报错，
                          // 不如一开始就说清楚它为什么不能删。
                          <span
                            className="ml-auto text-muted/60 cursor-not-allowed"
                            title="公共经验，所有账号共享，不可删除"
                          >
                            公共 · 不可删除
                          </span>
                        ) : (
                          <button
                            onClick={() => remove(e.id, e.title)}
                            className="ml-auto text-muted hover:text-[var(--danger)]"
                          >
                            删除该条目
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-sm text-muted mt-4">
            {isSearch
              ? `未找到与「${query}」相关的条目。换个说法再试，或确认这条经验是否已入库。`
              : sourceFilter !== 'all'
                ? '该来源下暂无条目。'
                : '暂无条目。办案中产生的经验可在案件「评估结果」页底部一键沉淀到本库。'}
          </p>
        )}
      </div>

      <SlideOver open={addOpen} onClose={() => setAddOpen(false)} title="新增经验条目" widthClass="w-[36rem]">
        <div className="space-y-4">
          <div className="flex items-center gap-3 pb-1">
            <label className="flex items-center gap-1.5 text-xs text-muted hover:text-fg cursor-pointer transition-colors">
              <input
                type="file"
                accept=".txt,.md,.docx,.pdf"
                className="hidden"
                disabled={importBusy}
                onChange={async (e) => {
                  const file = e.target.files?.[0]
                  if (file) await importToEditor(file)
                  e.target.value = ''
                }}
              />
              📎 从文件导入
            </label>
            {importBusy && <span className="text-xs text-muted">导入中…</span>}
            <span className="text-[11px] text-muted">抽取后填入下方，可校对再入库（仅文档类）</span>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1.5">标题</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="如：杭州中院类案判赔经验"
              className="w-full border border-line rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-brand"
            />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1.5">内容</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="类案数据、办案心得、判赔口径、抗辩应对经验…"
              rows={14}
              className="w-full border border-line rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-brand resize-none"
            />
            <p className="text-[11px] text-muted mt-1.5">
              长文会自动分块向量化，入库即刻可被检索与召回命中。
            </p>
          </div>
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={add}
              disabled={saving || !title.trim() || !content.trim()}
              className="bg-brand hover:bg-fg text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
            >
              {saving ? '入库中…' : '入个人知识库'}
            </button>
            <button onClick={() => setAddOpen(false)} className="text-sm text-muted hover:text-fg px-3 py-2">
              取消
            </button>
          </div>
        </div>
      </SlideOver>

      <SlideOver open={batchOpen} onClose={() => setBatchOpen(false)} title="批量导入文件" widthClass="w-[36rem]">
        <div className="space-y-4">
          <p className="text-xs text-muted">
            支持 .txt / .md / .docx / .pdf，每个文件生成一条「手动录入」经验（标题=文件名去扩展名）。
            单文件失败不影响其他文件。
          </p>
          <input
            type="file"
            multiple
            accept=".txt,.md,.docx,.pdf"
            onChange={(e) => setBatchFiles(Array.from(e.target.files ?? []))}
            className="block w-full text-sm text-muted"
          />
          {batchFiles.length > 0 && (
            <ul className="text-xs text-muted space-y-1 max-h-40 overflow-auto">
              {batchFiles.map((f) => (
                <li key={f.name}>{f.name}</li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={startBatch}
              disabled={batchBusy || batchFiles.length === 0}
              className="bg-brand hover:bg-fg text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
            >
              {batchBusy ? '导入中…' : `开始导入（${batchFiles.length} 个）`}
            </button>
            <button onClick={() => setBatchOpen(false)} className="text-sm text-muted hover:text-fg px-3 py-2">
              关闭
            </button>
          </div>
          {batchResult && (
            <div className="bg-canvas rounded-lg p-3 text-xs space-y-1">
              <div className="font-medium">
                完成：成功 {batchResult.ok} 条 / 失败 {batchResult.failed} 条
              </div>
              {batchResult.results.map((r, i) => (
                <div key={i} className={r.ok ? 'text-[var(--success)]' : 'text-[var(--danger)]'}>
                  {r.ok ? '✓' : '✗'} {r.filename}
                  {r.ok ? '' : ` — ${r.error}`}
                </div>
              ))}
            </div>
          )}
        </div>
      </SlideOver>
    </div>
  )
}

function FilterChip({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full transition-colors ${
        active ? 'text-fg font-medium bg-canvas' : 'text-muted hover:text-fg'
      }`}
    >
      {children}
    </button>
  )
}
