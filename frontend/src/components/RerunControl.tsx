import { useState } from 'react'

export interface RerunResult {
  effect: string
  stale_nodes: { node: string; label: string; reason: string }[]
}

interface Props {
  label: string
  hint?: string
  busy: boolean
  /** 多个控件并排成组时不画分隔线，由外层统一加 */
  compact?: boolean
  /** 触发按钮形态：pill（默认，卡片内）/ link（文字行内，如结果页的纯文字小节） */
  variant?: 'pill' | 'link'
  onRerun: (guidance: string) => Promise<RerunResult>
}

/**
 * 节点级重跑控件（方案 §6.4）
 *
 * 用法：挂在任一维度卡片上。点「重跑」展开引导意见输入框——
 * 引导会注入 user_viewpoints，自动影响后续所有节点，而不只是本节点。
 * 这是「人机协作」区别于「重新跑一遍全流程」的关键：改一处，只重算受影响的下游。
 */
export default function RerunControl({ label, hint, busy, compact, variant = 'pill', onRerun }: Props) {
  const [open, setOpen] = useState(false)
  const [guidance, setGuidance] = useState('')
  const [result, setResult] = useState<RerunResult | null>(null)
  const [error, setError] = useState('')

  const submit = async () => {
    setError('')
    setResult(null)
    try {
      const r = await onRerun(guidance.trim())
      setResult(r)
      setOpen(false)
      setGuidance('')
    } catch (e) {
      setError(String(e))
    }
  }

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
              onClick={() => { setOpen(false); setGuidance(''); setError('') }}
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
