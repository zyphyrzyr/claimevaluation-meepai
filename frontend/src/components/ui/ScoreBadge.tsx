import type { Thresholds } from '../../lib/tiers'
import { tierOf, recoveryTierOf, RECOVERY_TIER_LABEL } from '../../lib/tiers'
import { cn } from '../../lib/utils'

const TIER_LABEL: Record<string, string> = { go: '强', patch: '待补强', block: '偏弱' }

/** 回款档位 → 配色档（ok 绿 / neutral 黄 / weak 红），配色仍走同一套 tier-* */
const RECOVERY_TIER_CLASS: Record<string, string> = {
  ok: 'go',
  neutral: 'patch',
  weak: 'block',
}

/**
 * 分数徽标（大字分数 + 档位色 + 档位文案，照后端阈值上色）
 *
 * kind='recovery' 用于回款能力：它是概率型指标，档位锚在「记录干净」的基准分上，
 * 不能套决策分的 62/78——否则无信号会被判成「偏弱」，与规则表语义相反。
 */
export function ScoreBadge({
  score,
  t,
  className,
  kind = 'score',
}: {
  score: number | null | undefined
  t: Thresholds
  className?: string
  kind?: 'score' | 'recovery'
}) {
  if (kind === 'recovery') {
    const rt = recoveryTierOf(score, t)
    if (rt == null) return <span className="text-xs text-muted">未评分</span>
    return (
      <span className={cn('inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-sm font-medium', `tier-${RECOVERY_TIER_CLASS[rt]}`, className)}>
        <span className="text-base font-semibold leading-none">{score}</span>
        <span className="text-[10px]">{RECOVERY_TIER_LABEL[rt]}</span>
      </span>
    )
  }

  const tier = tierOf(score, t)
  if (tier == null) return <span className="text-xs text-muted">未评分</span>
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-sm font-medium', `tier-${tier}`, className)}>
      <span className="text-base font-semibold leading-none">{score}</span>
      <span className="text-[10px]">{TIER_LABEL[tier]}</span>
    </span>
  )
}
