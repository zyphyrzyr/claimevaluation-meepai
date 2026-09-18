import { useState } from 'react'

export interface RerunResult {
  effect: string
  stale_nodes: { node: string; label: string; reason: string }[]
  /** 级联重跑时真正被重算的下游节点（前端据此提示「已重算」） */
  rerun_nodes?: { node: string; label: string }[]
  cascade?: boolean
}

interface Props {
  label: string
  hint?: string
  busy: boolean
  /** 多个控件并排成组时不画分隔线，由外层统一加 */
  compact?: boolean
  /** 触发按钮形态：pill（默认，卡片内）/ link（文字行内，如结果页的纯文字小节） */
  variant?: 'pill' | 'link'
  /** 开启「仅本节点」时，提示用户哪些下游维度将标为参考（沿用旧分、不重算） */
  downstreamLabels?: string[]
  onRerun: (guidance: string, cascade: boolean) => Promise<RerunResult>
}

/**
 * 节点级重跑控件（方案 §6.4）
 *
 * 用法：挂在任一维度卡片上。点「重跑」展开引导意见输入框——
 * 引导会注入 user_viewpoints，自动影响后续所有节点，而不只是本节点。
 * 这是「人机协作」区别于「重新跑一遍全流程」的关键：改一处，只重算受影响的下游。
 *
 * 级联开关（计划 C）：默认级联重跑——本节点重跑后自动按 DAG 重算下游 LLM 节点。
 * 勾选「仅本节点」则下游全部标 stale（沿用旧分、结论标「参考」），省一次成本/离线可用。
 */
export default function RerunControl({ label, hint, busy, compact, variant = 'pill', downstreamLabels, onRerun }: Props) {
  const [open, setOpen] = useState(false)
  const [guidance, setGuidance] = useState('')
  const [onlyNode, setOnlyNode] = useState(false)
  const [result, setResult] = useState<RerunResult | null>(null)
  const [error, setError] = useState('')

  const submit = async () => {
    setError('')
    setResult(null)
    try {
      const r = await onRerun(guidance.trim(), !onlyNode)
      setResult(r)
      setOpen(false)
      setGuidance('')
      setOnlyNode(false)
    } catch (e) {
      setError(String(e))
    }
  }

  const cascade = !onlyNode
  const showDownstreamWarn = !cascade && (downstreamLabels?.length ?? 0) > 0

  return (
    <div className={compact ? '' : 'mt-3 pt-3 border-t border-line'}>
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          disabled={busy}
          className={
            variant === 'link'
              ? 'text-xs text-muted hover:text-fg underline underline-offset-2 transition-colors disabled:opacity-40'
              : 'text-xs text-muted hover:text-fg border border-line hover:border-fg px-2.5 py-1 rounded-md transition-colors disabled:opacity-40'
          }
        >
          {busy ? '重跑中…' : `重跑「${label}」`}
        </button>
      ) : (
        <div className="space-y-2">
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={3}
            placeholder={`给「${label}」的引导意见（可选）。例：对方商标 2024 年已被提撤三，请据此重新评估权利稳定性。`}
            className="w-full text-sm border border-line rounded-lg p-2.5 resize-none focus:outline-none focus:border-fg"
          />
          <label className="flex items-start gap-2 text-xs text-muted cursor-pointer select-none">
            <input
              type="checkbox"
              checked={onlyNode}
              onChange={(e) => setOnlyNode(e.target.checked)}
              className="mt-0.5 accent-[var(--brand)]"
            />
            <span>
              仅本节点（不级联重算下游）
              <span className="block text-[11px] text-muted/80">
                勾选后下游维度沿用旧分、结论标「参考」，省一次成本；不勾选则自动重算下游。
              </span>
            </span>
          </label>
          {showDownstreamWarn && (
            <div className="text-[11px] text-[var(--warning)] bg-[var(--warning-soft)] border border-[var(--warning-soft)] rounded-lg px-2.5 py-1.5">
              仅重跑本节点，以下下游维度将标为「参考」（沿用上次结果，不重算）：
              <span className="font-medium">{downstreamLabels!.join('、')}</span>
              。可在结果页逐个重跑它们以刷新。
            </div>
          )}
          <p className="text-[11px] text-muted">
            {hint ?? '引导意见会写入案件观点，自动影响本节点及其下游；纯规则环节（决策合成）将瞬时重算。'}
          </p>
          <div className="flex gap-2">
            <button
              onClick={submit}
              disabled={busy}
              className="bg-fg hover:opacity-90 text-canvas text-xs px-3 py-1.5 rounded-md transition-colors disabled:opacity-50"
            >
              确认重跑
            </button>
            <button
              onClick={() => { setOpen(false); setGuidance(''); setOnlyNode(false); setError('') }}
              className="text-xs text-muted px-3 py-1.5 rounded-md hover:bg-surface transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {error && <div className="mt-2 text-xs text-danger">{error}</div>}

      {result && (
        <div className="mt-2 space-y-1 text-xs">
          <div className="text-success">{result.effect}</div>
          {result.rerun_nodes && result.rerun_nodes.length > 0 && (
            <div className="text-success/80">
              已级联重算：{result.rerun_nodes.map((r) => r.label).join('、')}
            </div>
          )}
          {result.stale_nodes.length > 0 && (
            <div className="text-warning">
              待确认重跑：{result.stale_nodes.map((s) => s.label).join('、')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
