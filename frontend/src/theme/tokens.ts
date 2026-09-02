/**
 * TDesign 设计 token 映射（P2）
 * - 与 index.css 中 [data-theme] CSS 变量一一对应，作为 TS 侧（如 ECharts 配色、
 *   动态内联样式）引用 token 的唯一来源，避免在前端散落硬编码色值。
 * - 两主题共用同一组 key，靠 CSS 变量切换；此处只给"默认值"，运行时以 CSS 变量为准。
 */

export type ThemeName = 'light' | 'theater'

/** 语义色 token（与 index.css 变量名对应） */
export const semanticColors = {
  bg: 'var(--bg)',
  surface: 'var(--surface)',
  text: 'var(--text)',
  textMuted: 'var(--text-muted)',
  border: 'var(--border)',
  brand: 'var(--brand)',
  brand2: 'var(--brand-2)',
  success: 'var(--success)',
  warning: 'var(--warning)',
  danger: 'var(--danger)',
  info: 'var(--info)',
} as const

/** 结构 token（与 tailwind theme 共享，跨主题不变） */
export const structuralTokens = {
  radius: { sm: '4px', md: '8px', lg: '12px' },
  spacing: { xs: '4px', sm: '8px', md: '12px', lg: '16px', xl: '24px' },
  shadow: {
    card: '0 1px 2px rgba(13,13,13,0.04), 0 4px 12px rgba(13,13,13,0.04)',
  },
} as const

/**
 * 档位 → 语义色 key（供 TS 侧上色，如 ECharts 分数条）
 * 阈值由后端下发（api.result.thresholds.go / patch），前端只负责照档取色。
 */
export type Tier = 'go' | 'patch' | 'block'
export function tierVar(tier: Tier): string {
  return tier === 'go' ? semanticColors.success
    : tier === 'patch' ? semanticColors.warning
    : semanticColors.danger
}
