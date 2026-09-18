import { useEffect } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  /** 危险操作：确认按钮用危险色，并更醒目 */
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 轻量二次确认弹窗：复用现有 CSS 变量（bg-surface / border-line / text-fg …），
 * 无第三方依赖。用于「重新评估会清空前轮结果」这类破坏性但需用户明确授权的动作。
 */
export default function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  cancelText = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md bg-surface border border-line rounded-xl p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <h3 className="text-sm font-medium text-fg mb-2">{title}</h3>
        <p className="text-sm text-muted leading-relaxed mb-5 whitespace-pre-line">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm border border-line text-muted hover:bg-line/40 transition-colors"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className={
              'px-4 py-2 rounded-lg text-sm font-medium transition-colors ' +
              (danger
                ? 'bg-[var(--danger)] text-white hover:opacity-90'
                : 'bg-fg text-canvas hover:opacity-90')
            }
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
