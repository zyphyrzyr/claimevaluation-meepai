import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../lib/utils'

/**
 * 右侧滑出抽屉：带遮罩、标题、关闭按钮。
 * 用 framer-motion 做弹簧滑入；打开时锁 body 滚动避免背景穿透。
 *
 * Esc 关闭支持嵌套：维护一个模块级栈，按键时只触发栈顶（最上层）抽屉，
 * 所以「抽屉里再开预览面板」时，第一次 Esc 关预览、第二次 Esc 才关抽屉。
 */

const escStack: Array<() => void> = []
if (typeof window !== 'undefined') {
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && escStack.length) {
      e.preventDefault()
      escStack[escStack.length - 1]()
    }
  })
}

export default function SlideOver({
  open,
  onClose,
  title,
  panelRef,
  children,
  widthClass,
  panelZClass,
  bodyClassName,
}: {
  open: boolean
  onClose: () => void
  title: string
  /** 暴露可滚动面板本身：抽屉内的章节导航需要它作为滚动容器来判断当前位置 */
  panelRef?: React.Ref<HTMLDivElement>
  children: React.ReactNode
  /** 面板宽度类，覆盖默认 76rem（预览面板用它缩到约 52rem） */
  widthClass?: string
  /** 面板层叠 z 值，默认 z-50；嵌套弹窗（抽屉里再开预览）需更高层级 */
  panelZClass?: string
  /** 主体内容区类名：默认 p-6 且内部滚动；预览面板传 p-0 flex flex-col 以撑满填充 */
  bodyClassName?: string
}) {
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const handler = () => onCloseRef.current()
    escStack.push(handler)
    return () => {
      const idx = escStack.lastIndexOf(handler)
      if (idx >= 0) escStack.splice(idx, 1)
    }
  }, [open])

  useEffect(() => {
    if (open) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [open])

  // 抽屉是纯客户端构件：portal 必须有 DOM。服务端渲染时直接返回空，
  // 否则任何用到本组件的页面一进 renderToString 就抛 document is not defined。
  // （守卫放在全部 Hook 之后，不破坏 Hook 顺序。）
  if (typeof document === 'undefined') return null

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/20 z-40"
          />
          <motion.div
            ref={panelRef}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className={cn(
              'fixed inset-y-0 right-0 max-w-full bg-canvas border-l border-line shadow-2xl z-50 flex flex-col',
              widthClass ?? 'w-[76rem]',
              panelZClass ?? '',
            )}
          >
            <div className="px-6 py-5 border-b border-line flex items-center justify-between shrink-0">
              <h2 className="text-lg font-medium text-fg">{title}</h2>
              <button
                onClick={onClose}
                aria-label="关闭"
                className="text-muted hover:text-fg text-xl leading-none"
              >
                ✕
              </button>
            </div>
            <div className={cn('flex-1 min-h-0 overflow-y-auto p-6', bodyClassName)}>
              {children}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}
