/** 分数档位工具（P2，组件层与页面共用，避免重复实现） */
export interface Thresholds {
  go: number
  patch: number
  quadrant_mid: number
  power_mean_p: number
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
