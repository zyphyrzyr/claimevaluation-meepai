import { cn } from '../lib/utils'

/**
 * 章节导航 / 步骤选择器：与左侧边栏同款视觉（text-sm / py-2 / muted → fg，当前项通过 font-medium + text-fg 高亮，无左侧导轨）。
 * 受控组件：`active` 与 `onSelect` 全由父级决定，因此同一个组件能支撑两种完全不同的模式——
 *
 *   A. 步骤切换（案件详情：单块逐步表单）
 *      `active` 由父级 state 直接决定，点击 = 切换右侧显示哪一块，页面不滚动。
 *   B. 锚点跳转（评估详情：长页整体滚动）
 *      内容一次全渲染，`active` 来自父级的滚动高亮，点击 = 平滑滚到对应 section。
 *      这两套驱动逻辑都在调用方（CaseWorkbench），本组件不做任何假设。
 *
 * 两个刻意的设计取舍：
 *
 * 1) 纵向定位交给调用方（className）。整页场景用
 *    `mt-[calc(25vh+3rem)] sticky top-[calc(25vh+4.25rem)]`，抽屉里用 `sticky top-6`。
 *    mt 与 top 是两件事：mt 决定「自然位置」（未滚动时落在哪，要跟左侧边栏首项对齐），
 *    top 决定「粘住后停在哪」，两者相等才不会在触发瞬间跳位。
 *    注意 sticky 必须挂在本组件（内层）上，不能挂到外层网格项——网格项被拉伸到整行高度，
 *    自身没有可粘行程，sticky 会退化成一开始就贴住网格顶部。
 *    同一个组件因此能同时适配「整页」与「抽屉内」两种场景，不必分叉。
 *
 * 2) 本组件自身不做滚动监听。曾经做过 scroll-spy，但对场景 A（单块逐步表单）是无意义的开销；
 *    现在需要高亮跟随滚动的是场景 B，那套逻辑放在 CaseWorkbench 里，按标签启用。
 */
export default function SectionNav({
  sections,
  active,
  onSelect,
  className,
}: {
  /** 分区定义，取自 NewCaseForm 的 FORM_SECTIONS（单一事实源） */
  sections: readonly { id: string; label: string }[]
  /** 当前激活的分区 id（由父级维护） */
  active: string
  /** 点击切换分区 */
  onSelect: (id: string) => void
  /** 纵向定位与粘性偏移，由调用方按场景传入 */
  className?: string
}) {
  return (
    <nav className={cn('flex flex-col gap-1', className)}>
      {sections.map((s) => {
        const on = active === s.id
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => onSelect(s.id)}
            className={cn(
              'text-left text-sm py-2 pl-2.5 pr-3 rounded-md transition-colors',
              on ? 'text-fg font-medium' : 'text-muted hover:text-fg',
            )}
          >
            {s.label}
          </button>
        )
      })}
    </nav>
  )
}
