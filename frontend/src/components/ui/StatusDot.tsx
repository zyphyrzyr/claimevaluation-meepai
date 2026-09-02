import type { NodeState } from '../../lib/tiers'
import { cn } from '../../lib/utils'

/** 节点状态点（颜色走 index.css 语义类 status-*） */
export function StatusDot({ status, className }: { status?: NodeState; className?: string }) {
  return <span className={cn('status-dot', `status-${status ?? 'waiting'}`, className)} />
}
