import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'

/**
 * 右侧滑出抽屉：带遮罩、标题、关闭按钮。
 * 用 framer-motion 做弹簧滑入；打开时锁 body 滚动避免背景穿透。
 */
export default function SlideOver({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}) {
  useEffect(() => {
    if (open) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [open])

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
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="fixed inset-y-0 right-0 w-[64rem] max-w-full bg-canvas border-l border-line shadow-2xl z-50 overflow-y-auto"
          >
            <div className="px-6 py-5 border-b border-line flex items-center justify-between">
              <h2 className="text-lg font-medium text-fg">{title}</h2>
              <button
                onClick={onClose}
                aria-label="关闭"
                className="text-muted hover:text-fg text-xl leading-none"
              >
                ✕
              </button>
            </div>
            <div className="p-6">{children}</div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}
