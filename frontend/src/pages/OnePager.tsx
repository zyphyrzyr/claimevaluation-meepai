import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api, exportUrls } from '../api'

/**
 * 向上汇报一页纸摘要（独立视图）
 *
 * 与报告页里的摘要卡片是同一份数据，但这里是**给领导看**的形态：
 * 结论先行、一屏装下、可直接打印或另存 PDF。所以刻意不做交互——
 * 没有 diff、没有版本切换、没有悬浮顾问，避免打印时把界面控件也印进去。
 */

const LEVEL_STYLE: Record<string, { chip: string; bar: string }> = {
  green: { chip: 'bg-[var(--success)]', bar: 'bg-[var(--success)]' },
  yellow: { chip: 'bg-[var(--warning-soft)]0', bar: 'bg-[var(--warning-soft)]0' },
  red: { chip: 'bg-[var(--danger)]', bar: 'bg-[var(--danger)]' },
}

export default function OnePager() {
  const { id } = useParams<{ id: string }>()
  const [memo, setMemo] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    api.memo(id).then(setMemo).catch((e) => setError(String(e)))
  }, [id])

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!memo) return <div className="text-muted text-sm">加载中…</div>

  const op = memo.one_pager
  const s = memo.scores
  const style = LEVEL_STYLE[op.level] ?? { chip: 'bg-fg', bar: 'bg-fg' }

  return (
    <div className="space-y-4">
      {/* 屏幕上的操作条；打印时隐藏 */}
      <div className="flex items-center justify-between flex-wrap gap-3 print:hidden">
        <Link to={`/cases/${id}/report`} className="text-sm text-muted hover:text-fg">
          ← 返回完整备忘录
        </Link>
        <div className="flex items-center gap-3">
          <button
            onClick={() => window.print()}
            className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
          >
            打印 / 另存 PDF
          </button>
          <a
            href={exportUrls.memoDocx(id!)}
            className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
          >
            下载 Word
          </a>
        </div>
      </div>

      {/* 一页纸本体：A4 比例，打印时占满页面 */}
      <div className="bg-white rounded-xl border border-line print:border-0 print:rounded-none mx-auto max-w-[820px] p-8 space-y-6">
        <header className="border-b-2 border-line pb-4">
          <div className="text-xs text-muted tracking-wide">主诉评估 · 向上汇报摘要</div>
          <h1 className="text-2xl font-medium mt-1">{memo.case_name}</h1>
          <div className="text-sm text-muted mt-1">
            {memo.cause_type} ｜ 业务目标「{memo.goal_type}」 ｜ 生成日期{' '}
            {new Date().toLocaleDateString('zh-CN')}
          </div>
        </header>

        {/* 结论 */}
        <section className="flex items-start justify-between gap-6 flex-wrap">
          <div>
            <div className="text-xs text-muted mb-1">评估结论</div>
            <div className="text-xl font-semibold flex items-center gap-3">
              <span className={`w-2.5 h-2.5 rounded-full ${style.chip}`} />
              {op.conclusion}
            </div>
            {op.quadrant && <div className="text-sm text-muted mt-1">{op.quadrant}</div>}
          </div>
          <div className="text-right">
            <div className="text-xs text-muted">主诉决策分</div>
            <div className="text-5xl font-semibold leading-none mt-1">
              {s.final ?? '—'}
            </div>
          </div>
        </section>

        {/* 二维评分 */}
        <section>
          <div className="grid grid-cols-4 gap-4">
            {[
              { label: '法律可行性', value: op.scores.legal_feasibility },
              { label: '业务预期', value: op.scores.business_expectation },
              { label: '主诉决策分', value: op.scores.final },
              { label: '置信度', value: op.scores.confidence, suffix: '%' },
            ].map((m) => (
              <div key={m.label} className="border border-line rounded-lg p-3">
                <div className="text-[11px] text-muted">{m.label}</div>
                <div className="text-2xl font-semibold mt-0.5">
                  {m.value ?? '—'}
                  <span className="text-xs text-muted font-normal">{m.suffix ?? ''}</span>
                </div>
                {typeof m.value === 'number' && (
                  <div className="h-1 bg-surface rounded-full mt-2">
                    <div
                      className={`h-1 rounded-full ${style.bar}`}
                      style={{ width: `${Math.min(100, m.value)}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
          <p className="text-[11px] text-muted mt-2">
            主诉决策分为法律可行性与业务预期的均衡水平（幂平均），任一维度过低即显著拉低总分。
          </p>
        </section>

        {/* 三条核心理由 */}
        <section>
          <h2 className="text-sm font-medium mb-2">核心理由</h2>
          <ol className="space-y-2">
            {op.reasons.map((r: string, i: number) => (
              <li key={i} className="flex gap-2.5 text-sm text-muted">
                <span className="text-muted shrink-0">{i + 1}.</span>
                <span>{r}</span>
              </li>
            ))}
          </ol>
        </section>

        {/* 行动建议 */}
        {op.actions?.length > 0 && (
          <section>
            <h2 className="text-sm font-medium mb-2">行动建议</h2>
            <ul className="space-y-1.5">
              {op.actions.map((a: string, i: number) => (
                <li key={i} className="flex gap-2.5 text-sm text-muted">
                  <span className="text-muted shrink-0">□</span>
                  <span>{a}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* 风险提示 */}
        {memo.red_flags?.length > 0 && (
          <section>
            <h2 className="text-sm font-medium mb-2">需关注的风险</h2>
            <ul className="space-y-1.5">
              {memo.red_flags
                .filter((f: any) => f.severity !== 'pass')
                .map((f: any, i: number) => (
                  <li key={i} className="flex gap-2.5 text-sm text-muted">
                    <span className="shrink-0">{f.severity === 'block' ? '⛔' : '⚠️'}</span>
                    <span>
                      <span className="font-medium">{f.rule_name}</span>：{f.reason}
                    </span>
                  </li>
                ))}
            </ul>
          </section>
        )}

        <footer className="border-t border-line pt-3 text-[11px] text-muted leading-relaxed">
          本摘要由 Soft IP 主诉评估系统生成，AI 辅助评估结果仅供内部决策参考，不构成正式法律意见。
          完整论证见配套《主诉评估决策备忘录》。
        </footer>
      </div>
    </div>
  )
}
