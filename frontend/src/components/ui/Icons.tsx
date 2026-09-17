import type { SVGProps } from 'react'

/**
 * 线性图标：纯内联 SVG，12~14px 尺寸下靠 fill 而非 stroke 保清晰度。
 *
 * 为什么不用 emoji（⏸ ▶ ⏹）：
 *   macOS 会把它们按「彩色 emoji」渲染，字色/基线都和旁边的正文对不上，
 *   同一套字重在不同系统差别极大，等同于把排版交给系统字体决定。
 *   这里统一用 currentColor，天然跟着父级文字色走（静默态是 muted，危险态是 danger）。
 */
type IconProps = SVGProps<SVGSVGElement>

/** 暂停：两根圆角竖条 */
export function PauseIcon(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3 shrink-0" aria-hidden {...props}>
      <rect x="6.6" y="4.6" width="3.6" height="14.8" rx="1.2" />
      <rect x="13.8" y="4.6" width="3.6" height="14.8" rx="1.2" />
    </svg>
  )
}

/** 继续：右向三角 */
export function PlayIcon(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-3 w-3 shrink-0" aria-hidden {...props}>
      <path d="M8.2 5.3a.95.95 0 0 1 1.44-.81l8.9 5.6a1.15 1.15 0 0 1 0 1.94l-8.9 5.6a.95.95 0 0 1-1.44-.81V5.3Z" />
    </svg>
  )
}

/** 终止：圆角方块（比 × 更贴近「停止」，比 emoji 可控） */
export function StopIcon(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-2.5 w-2.5 shrink-0" aria-hidden {...props}>
      <rect x="5.6" y="5.6" width="12.8" height="12.8" rx="3" />
    </svg>
  )
}

/** 警告：描边圆形感叹号，用于终止前的就地二次确认条 */
export function AlertIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      className="h-3.5 w-3.5 shrink-0"
      aria-hidden
      {...props}
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.8v4.9" />
      <path d="M12 16.3h.01" />
    </svg>
  )
}
