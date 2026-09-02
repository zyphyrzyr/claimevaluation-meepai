import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

export type BadgeVariant =
  | 'default' | 'brand' | 'success' | 'warning' | 'danger' | 'info'

const variants: Record<BadgeVariant, string> = {
  default: 'border-line text-muted bg-surface',
  brand: 'border-[var(--brand)] text-[var(--brand)] bg-[var(--brand-soft,#f3f3f3)]',
  success: 'text-[var(--success)] bg-[var(--success-soft)] border border-[var(--success-soft)]',
  warning: 'text-[var(--warning)] bg-[var(--warning-soft)] border border-[var(--warning-soft)]',
  danger: 'text-[var(--danger)] bg-[var(--danger-soft)] border border-[var(--danger-soft)]',
  info: 'text-[var(--info)] bg-[var(--info)]/10 border border-[var(--info)]/20',
}

/** 语义徽标（状态 / 档位 / 红线标签统一走 token） */
export function Badge({ variant = 'default', className, ...props }: HTMLAttributes<HTMLSpanElement> & { variant?: BadgeVariant }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
        variants[variant],
        className,
      )}
      {...props}
    />
  )
}
