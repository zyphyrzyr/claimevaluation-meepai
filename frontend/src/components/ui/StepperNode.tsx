import type { ReactNode } from 'react'
import type { NodeState } from '../../lib/tiers'
import { cn } from '../../lib/utils'
import { StatusDot } from './StatusDot'

/** 步骤节点容器：状态点 + 标题 + 右侧槽（重跑/标签）+ 详细内容 */
export function StepperNode({
  status,
  label,
  right,
  children,
  className,
}: {
  status?: NodeState
  label: string
  right?: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-xl border bg-surface p-5',
        status === 'stale' ? 'border-dashed border-line' : 'border-line',
        className,
      )}
    >
      <div className="flex items-center gap-2.5 mb-3">
        <StatusDot status={status} />
        <span className="text-sm font-medium text-fg">{label}</span>
        {right}
        <div className="flex-1" />
      </div>
      {children}
    </div>
  )
}
