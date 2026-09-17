import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'

/**
 * 登录 / 注册弹窗。
 *
 * 刻意做成**受控的纯展示组件**（不读 AuthContext）：
 * 状态与副作用都在 AuthProvider 里，这里只负责渲染与收集输入。
 * 好处是它能被单独渲染出来做冒烟断言（未登录态 / 错误态都能构造），
 * 不必先伪造一整套 Provider。
 */

export type LoginMode = 'login' | 'register'

export default function LoginModal({
  open,
  onClose,
  mode: initialMode = 'login',
  reason = '',
  busy = false,
  error = '',
  onLogin,
  onRegister,
}: {
  open: boolean
  onClose: () => void
  /** 初始页签：被 401 弹出来时是登录，用户主动点「注册」时切过去 */
  mode?: LoginMode
  /** 为什么弹这个框（「登录后才能新建案件」），显示在标题下方 */
  reason?: string
  /** 请求进行中：禁用按钮，避免连点发出一堆注册请求 */
  busy?: boolean
  /** 服务端返回的错误文案，就地显示在表单里 */
  error?: string
  onLogin: (email: string, password: string) => void
  onRegister: (email: string, password: string, displayName: string) => void
}) {
  const [mode, setMode] = useState<LoginMode>(initialMode)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const emailRef = useRef<HTMLInputElement>(null)

  // 外部切换初始页签（重新打开时）要跟过来
  useEffect(() => {
    if (open) setMode(initialMode)
  }, [open, initialMode])

  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
      // 自动聚焦：框已经弹出来了，再多一次点击是白给的摩擦
      const t = setTimeout(() => emailRef.current?.focus(), 80)
      return () => {
        clearTimeout(t)
        document.body.style.overflow = ''
      }
    }
  }, [open])

  // portal 必须有 DOM；守卫放在全部 Hook 之后，不破坏 Hook 顺序
  if (typeof document === 'undefined') return null

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    if (mode === 'login') onLogin(email, password)
    else onRegister(email, password, displayName)
  }

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          {/* 外壳兼作遮罩：**居中交给 flex，动画只留给内层卡片**。
              这两件事以前压在同一元素上，而 motion 的 y 动画会写成内联
              transform，把 Tailwind 的 -translate-x-1/2 整条覆盖掉——
              结果只剩 left/top:50%，弹窗左上角落在页面正中（右下偏半个身位）。
              顺带用 p-4 + overflow-y-auto 兜住矮窗口：能滚，不会顶出屏幕。 */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/30 z-[60] flex items-center justify-center p-4 overflow-y-auto"
          >
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ duration: 0.18 }}
              // 嵌套之后必须挡住冒泡，否则点卡片会被外壳当成「点外部关闭」
              onClick={(e) => e.stopPropagation()}
              className="w-[22rem] max-w-full bg-canvas border border-line rounded-lg shadow-2xl p-6"
            >
              <div className="flex items-center justify-between mb-1">
                <h2 className="text-base font-medium text-fg">
                  {mode === 'login' ? '登录' : '注册'}
                </h2>
                <button onClick={onClose} aria-label="关闭" className="text-muted hover:text-fg text-lg leading-none">
                  ✕
                </button>
              </div>

              {reason && <p className="text-xs text-muted mb-4">{reason}</p>}
              {!reason && <div className="mb-4" />}

              <form onSubmit={submit} className="space-y-3">
                <div>
                  <label className="block text-xs text-muted mb-1">邮箱</label>
                  <input
                    ref={emailRef}
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                    className="w-full px-3 py-2 text-sm rounded-md border border-line bg-surface text-fg outline-none focus:border-[var(--brand)]"
                  />
                </div>

                {mode === 'register' && (
                  <div>
                    <label className="block text-xs text-muted mb-1">
                      称呼 <span className="text-muted/70">（选填）</span>
                    </label>
                    <input
                      type="text"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder="怎么称呼你"
                      className="w-full px-3 py-2 text-sm rounded-md border border-line bg-surface text-fg outline-none focus:border-[var(--brand)]"
                    />
                  </div>
                )}

                <div>
                  <label className="block text-xs text-muted mb-1">密码</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={mode === 'register' ? '至少 6 位' : ''}
                    required
                    className="w-full px-3 py-2 text-sm rounded-md border border-line bg-surface text-fg outline-none focus:border-[var(--brand)]"
                  />
                </div>

                {error && (
                  <p className="text-xs text-[var(--danger)] bg-[var(--danger-soft)] border border-[var(--danger-line)] rounded px-2 py-1.5">
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={busy}
                  className="w-full py-2 text-sm rounded-md bg-[var(--brand)] text-white disabled:opacity-40"
                >
                  {busy ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}
                </button>
              </form>

              <div className="mt-4 text-xs text-muted text-center">
                {mode === 'login' ? (
                  <>
                    还没有账号？
                    <button onClick={() => setMode('register')} className="text-fg underline ml-1">
                      注册一个
                    </button>
                  </>
                ) : (
                  <>
                    已经有账号？
                    <button onClick={() => setMode('login')} className="text-fg underline ml-1">
                      去登录
                    </button>
                  </>
                )}
              </div>

              <p className="mt-3 text-[11px] text-muted text-center leading-relaxed">
                未登录也能浏览公共示例案件与经验库，
                <br />
                登录后才能建自己的案件、跑评估。
              </p>
            </motion.div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}
