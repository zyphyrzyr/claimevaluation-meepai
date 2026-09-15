import { cn } from '../lib/utils'

/**
 * 章节导航 / 步骤选择器：与左侧边栏同款视觉（text-sm / py-2 / muted → fg，当前项通过 font-medium + text-fg 高亮，无左侧导轨）。
 * 受控组件：当前步骤由父级 state 驱动（不再做 scroll-spy），点击直接切换 activeStep。
 *
 * 两个刻意的设计取舍：
 *
 * 1) 纵向定位交给调用方（className）。整页场景由**外层网格项**承担 sticky（本组件自身不再加 mt），
 *    偏移用 `top-[calc(25vh+3rem)]` 与左侧边栏首项对齐；抽屉里用 `sticky top-6`。
 *    sticky 挂在外层而非本组件，是因为本组件的父元素只有导航本身那么高，会让它没有可粘行程。
 *    同一个组件因此能同时适配「整页」与「抽屉内」两种场景，不必分叉。
 *
 * 2) 不再做滚动高亮——右侧工作区改为限高、内部滚动的单块逐步表单，页面整体不再随内容滚动，
 *    导航天然固定不动；当前项只表示「正在编辑哪一块」，由 active 直接决定。
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
