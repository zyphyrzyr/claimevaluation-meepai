import { useEffect, useState } from 'react'
import { KnowledgeEntryItem, SearchHit, knowledgeApi } from '../api'

/**
 * 全局经验库（§7 RAG 双集合分库之一）
 * 来源 B 手动录入 / D 独立建库；案件材料库在各案件评估准备页管理
 * 支持语义检索演示（与评估链路同一召回通道）
 */

const SOURCE_BADGE: Record<string, string> = {
  B: 'bg-blue-50 text-blue-700',
  C: 'bg-purple-50 text-purple-700',
  D: 'bg-teal-50 text-teal-700',
}

export default function KnowledgeBase() {
  const [entries, setEntries] = useState<KnowledgeEntryItem[]>([])
  const [info, setInfo] = useState<{ backend: string; embedding: string } | null>(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const [expandId, setExpandId] = useState<string>('')

  const load = () => {
    knowledgeApi.entries({ scope: 'global' })
      .then(setEntries)
      .catch((e) => setError(String(e)))
  }

  useEffect(() => {
    load()
    knowledgeApi.info().then(setInfo).catch(() => {})
  }, [])

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
      load()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (id: string, name: string) => {
    if (!confirm(`删除经验库条目「${name}」？向量将同步清除。`)) return
    try {
      await knowledgeApi.deleteEntry(id)
      load()
    } catch (e) {
      setError(String(e))
    }
  }

  const search = async () => {
    if (!query.trim()) return
    setSearching(true)
    setHits(null)
    try {
      setHits(await knowledgeApi.search({ query: query.trim(), scope: 'global', top_k: 5 }))
    } catch (e) {
      setError(String(e))
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-medium">全局经验库</h1>
        <p className="text-sm text-ink/50 mt-1">
          跨案件沉淀的办案经验与类案数据，评估时可手动勾选注入、追问/模拟法庭自动召回
          {info && (
            <span className="ml-2 text-xs text-ink/35">
              向量后端 {info.backend} · {info.embedding}
            </span>
          )}
        </p>
      </div>

      {error && <div className="bg-red-50 text-red-700 rounded-lg p-3 text-sm">{error}</div>}

      <div className="grid lg:grid-cols-2 gap-5">
        {/* 新增条目 */}
        <div className="bg-white rounded-xl border border-ink/10 p-5">
          <h2 className="text-sm font-medium mb-3">新增经验条目</h2>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="标题，如：杭州中院类案判赔经验"
            className="w-full border border-ink/15 rounded-lg px-3 py-2 text-sm mb-2 focus:outline-none focus:border-ember/60"
          />
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="内容：类案数据、办案心得、判赔口径、抗辩应对经验…（长文自动分块向量化）"
            rows={5}
            className="w-full border border-ink/15 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:border-ember/60 resize-none"
          />
          <button
            onClick={add}
            disabled={saving || !title.trim() || !content.trim()}
            className="bg-ember hover:bg-ember-dark text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40 transition-colors"
          >
            {saving ? '入库中…' : '入全局经验库'}
          </button>
        </div>

        {/* 语义检索演示 */}
        <div className="bg-white rounded-xl border border-ink/10 p-5">
          <h2 className="text-sm font-medium mb-3">语义检索（与评估召回同一通道）</h2>
          <div className="flex gap-2 mb-3">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
              placeholder="如：杭州 商标 判赔水平"
              className="flex-1 border border-ink/15 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-ember/60"
            />
            <button
              onClick={search}
              disabled={searching || !query.trim()}
              className="bg-ink hover:bg-ink-light text-white px-4 rounded-lg text-sm disabled:opacity-40 transition-colors"
            >
              {searching ? '检索中' : '检索'}
            </button>
          </div>
          {hits && (hits.length ? (
            <div className="space-y-2">
              {hits.map((h) => (
                <div key={h.id} className="border border-ink/10 rounded-lg p-3 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{h.title}</span>
                    <span className="text-xs text-ink/40">{(h.score * 100).toFixed(0)}%</span>
                  </div>
                  <div className="text-xs text-ink/50 mt-1 line-clamp-2">{h.matched_chunk}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-ink/40">无命中</p>
          ))}
        </div>
      </div>

      {/* 条目列表 */}
      <div className="bg-white rounded-xl border border-ink/10 p-5">
        <h2 className="text-sm font-medium mb-3">经验条目（{entries.length}）</h2>
        {entries.length ? (
          <div className="space-y-2">
            {entries.map((e) => (
              <div key={e.id} className="border-b border-ink/5 pb-2">
                <div className="flex items-center gap-2">
                  <span className={`text-[11px] px-2 py-0.5 rounded-full ${SOURCE_BADGE[e.source_type] ?? 'bg-ink/10 text-ink/60'}`}>
                    {e.source_type_label}
                  </span>
                  <button
                    onClick={() => setExpandId(expandId === e.id ? '' : e.id)}
                    className="text-sm font-medium hover:text-ember flex-1 text-left"
                  >
                    {e.title}
                  </button>
                  <span className="text-[11px] text-ink/35">{e.chunk_count} 块</span>
                  <button
                    onClick={() => remove(e.id, e.title)}
                    className="text-xs text-ink/35 hover:text-red-600"
                  >
                    删除
                  </button>
                </div>
                {expandId === e.id && (
                  <div className="text-xs text-ink/60 mt-2 whitespace-pre-wrap leading-relaxed bg-ink-pale/50 rounded-lg p-3">
                    {e.content}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-ink/40">
            暂无条目。办案中产生的经验也可在案件备忘录页一键沉淀到本库。
          </p>
        )}
      </div>
    </div>
  )
}
