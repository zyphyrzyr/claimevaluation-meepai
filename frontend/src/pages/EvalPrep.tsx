import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type KnowledgeEntryItem, knowledgeApi } from '../api'
import { Card } from '../components/ui/Card'

/**
 * 评估准备阶段：勾选知识库条目注入全部 LLM 评估节点（评分链路手动勾选，保证可复现）。
 * 运行时状态（start → SSE）由父级 CaseWorkbench 持有，本组件仅负责材料选择与触发 onStart。
 */
export default function EvalPrep({
  caseId,
  caseDetail,
  evaluated,
  onStart,
  error,
}: {
  caseId: string
  caseDetail: any
  evaluated: boolean
  onStart: (selectedIds: string[]) => void
  error: string
}) {
  const [caseEntries, setCaseEntries] = useState<KnowledgeEntryItem[]>([])
  const [globalEntries, setGlobalEntries] = useState<KnowledgeEntryItem[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [descExpanded, setDescExpanded] = useState(false)

  useEffect(() => {
    knowledgeApi.caseEntries(caseId).then(setCaseEntries).catch(() => {})
    knowledgeApi.entries({ scope: 'global' }).then(setGlobalEntries).catch(() => {})
  }, [caseId])

  const toggle = (entryId: string) => {
    setSelected((s) => {
      const copy = new Set(s)
      copy.has(entryId) ? copy.delete(entryId) : copy.add(entryId)
      return copy
    })
  }

  const di = caseDetail?.context?.defendant_info ?? {}
  const desc = caseDetail?.case_description ?? ''
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
    <>
      <h1 className="text-xl font-medium mb-1">评估准备 · 参考材料注入</h1>
      <p className="text-sm text-muted mb-4">
        勾选的知识库条目将注入全部 LLM 评估节点（评分链路手动勾选，保证结果可复现）；
        右下角追问顾问与模拟法庭则会按需自动召回
        {evaluated && <span className="text-[var(--warning)]">（本案已有评估结果，重新评估将覆盖）</span>}
      </p>

      {/* 案件简述（完整信息见「案件详情」标签） */}
      <div className="bg-surface border border-line rounded-xl p-4 mb-5 text-sm">
        <span className="text-fg font-medium">{caseDetail?.name || '—'}</span>
        <span className="text-muted"> · {caseDetail?.cause_type || '—'} · {caseDetail?.goal_type || '—'}</span>
        {desc && (
          <div className="mt-2 text-muted">
            <span className={!descExpanded ? 'line-clamp-3' : ''}>{desc}</span>
            {desc.length > 120 && (
              <button
                onClick={() => setDescExpanded((v) => !v)}
                className="text-xs text-brand hover:underline ml-2"
              >
                {descExpanded ? '收起' : '展开全文'}
              </button>
            )}
          </div>
        )}
      </div>

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
        onClick={() => onStart(Array.from(selected))}
        className="mt-6 w-full bg-fg hover:opacity-90 text-canvas py-3 rounded-lg text-sm font-medium transition-colors"
      >
        开始评估（已勾选 {selected.size} 条参考材料）→
      </button>
    </>
  )
}
