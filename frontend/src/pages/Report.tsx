import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, knowledgeApi } from '../api'
import { diffLines, DiffRow } from '../utils/diff'

/**
 * 决策备忘录：结构化备忘录 + 一页纸摘要 + 版本快照（v1/v2 diff 对比）+ 庭审记录入口
 * + 本案要点一键沉淀到全局经验库（来源 C）
 */

const LEVEL_BADGE: Record<string, string> = {
  green: 'bg-green-100 text-green-800',
  yellow: 'bg-amber-100 text-amber-800',
  red: 'bg-red-100 text-red-800',
  block: 'bg-red-200 text-red-900',
}

export default function Report() {
  const { id } = useParams<{ id: string }>()
  const [memo, setMemo] = useState<any>(null)
  const [versions, setVersions] = useState<any[]>([])
  const [snapshotInfo, setSnapshotInfo] = useState('')
  const [error, setError] = useState('')
  const [showMarkdown, setShowMarkdown] = useState(false)
  const [saving, setSaving] = useState(false)

  // 版本 diff
  const [diffVersion, setDiffVersion] = useState<number | null>(null)
  const [diffRows, setDiffRows] = useState<DiffRow[] | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  // 经验沉淀
  const [depositInfo, setDepositInfo] = useState('')

  const openDiff = async (version: number) => {
    if (!id || !memo) return
    setDiffVersion(version)
    setDiffRows(null)
    setDiffLoading(true)
    try {
      const snap = await api.version(id, version)
      setDiffRows(diffLines(snap.markdown_content ?? '', memo.markdown ?? ''))
    } catch (e) {
      setError(String(e))
      setDiffVersion(null)
    } finally {
      setDiffLoading(false)
    }
  }

  const deposit = async () => {
    if (!id || !memo) return
    const content = [
      `结论：${memo.one_pager.conclusion}（决策分 ${memo.scores.final ?? '—'}）`,
      `法律可行性 ${memo.scores.legal_feasibility ?? '—'} 与 业务预期 ${memo.scores.business_expectation ?? '—'} 的均衡水平 ${memo.scores.final ?? '—'}；置信度 ${memo.confidence ?? '—'}%。`,
      ...memo.one_pager.reasons.map((r: string) => `· ${r}`),
    ].join('\n')
    try {
      await knowledgeApi.deposit(id, `${memo.case_name} 评估结论（${memo.cause_type}）`, content)
      setDepositInfo('已沉淀到全局经验库，后续案件可召回复用')
    } catch (e) {
      setError(String(e))
    }
  }

  const load = () => {
    if (!id) return
    Promise.all([api.memo(id), api.versions(id)])
      .then(([m, v]) => {
        setMemo(m)
        setVersions(v)
      })
      .catch((e) => setError(String(e)))
  }

  useEffect(load, [id])

  const snapshot = async () => {
    if (!id) return
    setSaving(true)
    try {
      const r = await api.snapshot(id)
      setSnapshotInfo(`已定稿为 v${r.version}`)
      load()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (error) return <div className="bg-red-50 text-red-700 rounded-lg p-4 text-sm">{error}</div>
  if (!memo) return <div className="text-ink/40 text-sm">加载中…</div>

  const s = memo.scores
  const op = memo.one_pager
  const rec = memo.recommendation

  return (
    <div className="space-y-5">
      {/* 头部 */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-medium">决策备忘录</h1>
          <p className="text-sm text-ink/50 mt-1">
            {memo.case_name} · {memo.cause_type} · 目标「{memo.goal_type}」
          </p>
        </div>
        <div className="flex items-center gap-3">
          {snapshotInfo && <span className="text-xs text-green-700">{snapshotInfo}</span>}
          <button
            onClick={snapshot}
            disabled={saving || s.final == null}
            className="border border-ink/20 text-ink px-4 py-2 rounded-lg text-sm hover:bg-ink-pale disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? '保存中…' : '定稿快照'}
          </button>
        </div>
      </div>

      {/* 版本切换 */}
      {versions.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-ink/50">历史定稿：</span>
          <button className="text-xs px-2.5 py-1 rounded-full bg-ink text-white">当前（实时）</button>
          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => openDiff(v.version)}
              className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                diffVersion === v.version
                  ? 'border-ember text-ember bg-ember/5'
                  : 'border-ink/20 text-ink/70 hover:border-ink/50'
              }`}
            >
              v{v.version} · {v.generated_at?.slice(5, 16)}
            </button>
          ))}
          <span className="text-[11px] text-ink/35">点击版本号查看与当前版的对比</span>
        </div>
      )}

      {/* 版本 diff 对比 */}
      {diffVersion != null && (
        <div className="bg-white rounded-xl border border-ink/10 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium">
              版本对比：v{diffVersion} <span className="text-ink/40">→</span> 当前（实时）
            </h2>
            <div className="flex items-center gap-3">
              <span className="text-[11px] text-ink/40">
                <span className="text-red-600">■ 删除</span>　<span className="text-green-700">■ 新增</span>
              </span>
              <button
                onClick={() => { setDiffVersion(null); setDiffRows(null) }}
                className="text-xs text-ink/50 hover:text-ink"
              >
                关闭对比
              </button>
            </div>
          </div>
          {diffLoading ? (
            <p className="text-sm text-ink/40 py-4">加载版本内容…</p>
          ) : diffRows ? (
            <div className="bg-ink-pale/40 rounded-lg p-4 text-xs font-mono leading-relaxed overflow-x-auto max-h-[480px] overflow-y-auto">
              {diffRows.map((r, i) => (
                <div
                  key={i}
                  className={
                    r.type === 'add'
                      ? 'text-green-700 bg-green-50'
                      : r.type === 'remove'
                        ? 'text-red-600 bg-red-50 line-through decoration-red-300'
                        : 'text-ink/60'
                  }
                >
                  {r.type === 'add' ? '+ ' : r.type === 'remove' ? '- ' : '  '}
                  {r.text || ' '}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {/* 一页纸摘要 */}
      <div className="bg-ink text-white rounded-xl p-6">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-xs text-white/50 mb-1">向上汇报 · 一页纸摘要</div>
            <div className="text-lg font-medium">
              {op.conclusion}
              {op.quadrant && <span className="text-white/50 text-sm ml-2">（{op.quadrant}）</span>}
            </div>
          </div>
          {op.level && (
            <span className={`text-xs px-3 py-1 rounded-full font-medium ${LEVEL_BADGE[op.level] ?? ''}`}>
              决策分 {op.scores.final ?? '—'}
            </span>
          )}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5">
          {[
            { label: '法律可行性', value: op.scores.legal_feasibility },
            { label: '业务预期', value: op.scores.business_expectation },
            { label: '主诉决策分', value: op.scores.final },
            { label: '置信度', value: op.scores.confidence, suffix: '%' },
          ].map((m) => (
            <div key={m.label}>
              <div className="text-xs text-white/50">{m.label}</div>
              <div className="text-2xl font-semibold mt-0.5">
                {m.value ?? '—'}
                <span className="text-xs text-white/40 font-normal">{m.suffix ?? ''}</span>
              </div>
            </div>
          ))}
        </div>
        <ul className="mt-5 space-y-1.5">
          {op.reasons.map((r: string, i: number) => (
            <li key={i} className="text-sm text-white/80">· {r}</li>
          ))}
        </ul>
        {op.actions?.length > 0 && (
          <div className="mt-4 pt-4 border-t border-white/15">
            <div className="text-xs text-ember font-medium mb-1.5">行动建议</div>
            {op.actions.map((a: string, i: number) => (
              <div key={i} className="text-sm text-white/80">· {a}</div>
            ))}
          </div>
        )}
      </div>

      {/* 核心指标 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-5">
        {[
          { label: '主诉决策分', value: s.final, hint: '法律可行性与业务预期的均衡水平' },
          { label: '模拟法庭修正系数', value: s.correction_coeff, hint: s.correction_coeff !== 1 ? '已回写' : '未进行/无修正' },
          { label: '评估置信度', value: memo.confidence, suffix: '%', hint: '证据完整度决定' },
          { label: '证据缺口', value: memo.evidence.gap_list.length, suffix: ' 项', hint: `完整度 ${memo.evidence.completeness ?? 0}%` },
        ].map((m) => (
          <div key={m.label} className="bg-white rounded-xl border border-ink/10 p-4">
            <div className="text-xs text-ink/50">{m.label}</div>
            <div className="text-2xl font-semibold mt-1">
              {m.value ?? '—'}
              <span className="text-xs text-ink/40 font-normal">{m.suffix ?? ''}</span>
            </div>
            <div className="text-[11px] text-ink/40 mt-1">{m.hint}</div>
          </div>
        ))}
      </div>

      {/* 建议 */}
      <div className="bg-white rounded-xl border border-ink/10 p-5">
        <h2 className="text-sm font-medium mb-2">评估建议</h2>
        <p className="text-sm text-ink/80">{rec?.recommendation ?? '—'}</p>
        {rec?.reason && <p className="text-sm text-ink/50 mt-1">{rec.reason}</p>}
        {rec?.actions?.length > 0 && (
          <ul className="mt-3 space-y-1">
            {rec.actions.map((a: string, i: number) => (
              <li key={i} className="text-sm text-ink/70">· {a}</li>
            ))}
          </ul>
        )}
      </div>

      {/* 证据缺口清单 */}
      <div className="bg-white rounded-xl border border-ink/10 p-5">
        <h2 className="text-sm font-medium mb-3">证据缺口清单（{memo.evidence.gap_list.length} 项）</h2>
        {memo.evidence.gap_list.length ? (
          <div className="space-y-2">
            {memo.evidence.gap_list.map((g: any) => (
              <div key={g.id} className="flex items-start gap-3 text-sm border-b border-ink/5 pb-2">
                <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
                  g.status === 'missing' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'
                }`}>
                  {g.status === 'missing' ? '缺失' : '不足'}
                </span>
                <div>
                  <div>{g.item}</div>
                  <div className="text-xs text-ink/40 mt-0.5">{g.suggestion ?? g.reason}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-ink/50">证据准备充足，无明显缺口</p>
        )}
      </div>

      {/* 底部操作 */}
      <div className="flex items-center gap-4 flex-wrap text-sm">
        {memo.moot.rounds.length > 0 && (
          <Link to={`/cases/${id}/moot`} className="text-ink/60 hover:underline">
            查看庭审记录（修正系数 {memo.moot.correction_coeff}）
          </Link>
        )}
        <button
          onClick={deposit}
          className="text-ember hover:underline font-medium"
          disabled={!!depositInfo}
        >
          {depositInfo || '沉淀本案要点到全局经验库 →'}
        </button>
        <button
          onClick={() => setShowMarkdown(!showMarkdown)}
          className="text-ink/60 hover:underline"
        >
          {showMarkdown ? '收起' : '展开'} Markdown 源码（Word 导出内容源）
        </button>
        <Link to={`/cases/${id}/dashboard`} className="text-ink/60 hover:underline">
          返回仪表盘
        </Link>
      </div>

      {showMarkdown && (
        <pre className="bg-white rounded-xl border border-ink/10 p-5 text-xs text-ink/70 whitespace-pre-wrap leading-relaxed">
          {memo.markdown}
        </pre>
      )}
    </div>
  )
}
