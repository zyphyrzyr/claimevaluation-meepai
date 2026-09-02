import type { Thresholds } from '../../lib/tiers'
import { tierOf } from '../../lib/tiers'
import { cn } from '../../lib/utils'

const TIER_LABEL: Record<string, string> = { go: '强', patch: '待补强', block: '偏弱' }

/** 分数徽标（大字分数 + 档位色 + 档位文案，照后端阈值上色） */
export function ScoreBadge({
  score,
  t,
  className,
}: {
  score: number | null | undefined
  t: Thresholds
  className?: string
}) {
  const tier = tierOf(score, t)
  if (tier == null) return <span className="text-xs text-muted">未评分</span>
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-sm font-medium', `tier-${tier}`, className)}>
      <span className="text-base font-semibold leading-none">{score}</span>
      <span className="text-[10px]">{TIER_LABEL[tier]}</span>
    </span>
  )
}
