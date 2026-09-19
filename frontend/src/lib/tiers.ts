/** 分数档位工具（P2，组件层与页面共用，避免重复实现） */
export interface Thresholds {
  go: number
  patch: number
  quadrant_mid: number
  power_mean_p: number
  /**
   * 回款能力专用基准与档位（后端下发）。
   * 它是概率型指标，中性点不等于决策分的 62/78——用决策分档位去卡它，
   * 「公开记录干净」会被判成「待补强/偏弱」，与规则表语义正好相反。
   */
  recovery_base?: number
  recovery_ok?: number
  recovery_weak?: number
}

/** 节点状态（与后端 dimension_results.status 对齐） */
export type NodeState =
  | 'waiting' | 'running' | 'ok' | 'failed' | 'blocked' | 'partial' | 'stale'

export type Tier = 'go' | 'patch' | 'block' | null

/** 照后端下发阈值把分数映射到档位（≥go 强 / ≥patch 待补强 / 否则偏弱） */
export function tierOf(score: number | null | undefined, t: Thresholds): Tier {
  if (score == null || Number.isNaN(score)) return null
  if (score >= t.go) return 'go'
  if (score >= t.patch) return 'patch'
  return 'block'
}

/**
 * 回款能力档位：锚点是「公开记录干净」的基准分（recovery_base），
 * 往下扣到 weak 以下才算回款困难，往上不动就是有保障。
 * 文件里的数字只在后端未下发时使用（老数据 / 接口降级），不是第二份事实源。
 */
export type RecoveryTier = 'ok' | 'neutral' | 'weak'

export const RECOVERY_TIER_LABEL: Record<RecoveryTier, string> = {
  ok: '回款较有保障',
  neutral: '中性',
  weak: '回款困难',
}

export function recoveryTierOf(
  score: number | null | undefined,
  t: Thresholds,
): RecoveryTier | null {
  if (score == null || Number.isNaN(score)) return null
  const ok = t.recovery_ok ?? 70
  const weak = t.recovery_weak ?? 45
  if (score >= ok) return 'ok'
  if (score < weak) return 'weak'
  return 'neutral'
}
