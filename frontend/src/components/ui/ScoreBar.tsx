import type { Thresholds } from '../../lib/tiers'
import { tierOf } from '../../lib/tiers'
import { tierVar } from '../../theme/tokens'
import { cn } from '../../lib/utils'

/** 分数进度条（横向，照档位上色；供仪表盘/对比视图复用） */
export function ScoreBar({
  score,
  t,
  className,
}: {
  score: number | null | undefined
  t: Thresholds
  className?: string
}) {
  const tier = tierOf(score, t)
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score))
  const color = tier ? tierVar(tier) : 'var(--muted)'
  return (
    <div className={cn('h-1.5 w-full rounded-full bg-line overflow-hidden', className)}>
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  )
}
