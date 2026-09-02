import { cn } from '../../lib/utils'

export type VerdictLevel = 'green' | 'yellow' | 'red' | 'block'

/** 决策结论横幅（照 level 取档位色，走 token） */
export function ConclusionCard({
  recommendation,
  reason,
  level,
  className,
}: {
  recommendation?: string
  reason?: string
  level?: VerdictLevel
  className?: string
}) {
  if (!recommendation) return null
  const cls =
    level === 'green' ? 'tier-go' : level === 'yellow' ? 'tier-patch' : 'tier-block'
  return (
    <div className={cn('verdict-banner', cls, className)}>
      <div className="text-sm font-semibold">{recommendation}</div>
      {reason && <div className="text-xs mt-1 opacity-80">{reason}</div>}
    </div>
  )
}
