import type { TargetAndTransition, Transition } from 'framer-motion'

/**
 * 「单块逐步」界面的统一切换动画：案件详情（NewCaseForm）与评估详情（EvalRun）共用。
 *
 * 抽成常量而不是两个文件各写一份，是为了让「两个标签的切换体感一致」变成
 * 结构上的保证——否则以后只改了其中一边，就会悄悄漂移。
 *
 * 配套要求：必须用 <AnimatePresence mode="wait"> 包裹。
 * mode="wait" 会等旧块淡出结束再挂载新块；缺了它两块会同时存在，高度按较高的一块撑开，
 * 切换时会看到一次明显的抖动。
 */
export const STEP_MOTION: {
  initial: TargetAndTransition
  animate: TargetAndTransition
  exit: TargetAndTransition
  transition: Transition
} = {
  initial: { opacity: 0, y: 7 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -7 },
  transition: { duration: 0.22, ease: 'easeOut' },
}
