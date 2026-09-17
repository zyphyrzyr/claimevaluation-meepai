import { useEffect, useRef, useState } from 'react'
import type { AuthUser } from '../api'

/**
 * 左侧导航栏左下角的登录状态块。
 *
 * 纯展示组件：不读 AuthContext，全部由 props 驱动——这样「未登录 / 已登录」
 * 两种形态都能被单独渲染出来做冒烟断言。
 *
 * 未登录时整块是个按钮（点它弹登录框）；已登录时点它展开一个小菜单
 * （只有「退出登录」一项）。菜单不做成常驻的，是为了不抢导航的注意力。
 */

function initials(user: AuthUser): string {
  const name = (user.display_name || user.email || '?').trim()
  // 中文取首字，英文取首字母——「张三」比「Z」更像本人
  return name.slice(0, 1).toUpperCase()
}

export default function SidebarUser({
  user,
  loading = false,
  onOpenLogin,
  onLogout,
}: {
  user: AuthUser | null
  /** 首屏 /auth/me 还没回来：先占位，避免闪一下「未登录」再跳成已登录 */
  loading?: boolean
  onOpenLogin: () => void
  onLogout: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const onDocClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenuOpen(false)
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  if (loading) {
    return (
      <div className="mt-auto px-4 py-4 border-t border-line">
        <div className="h-9 rounded-md bg-[var(--brand-soft)] animate-pulse" />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="mt-auto px-4 py-4 border-t border-line">
        <button
          onClick={onOpenLogin}
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-left text-sm text-muted hover:text-fg hover:bg-[var(--brand-soft)] transition-colors"
        >
          <span className="w-7 h-7 rounded-full border border-dashed border-line flex items-center justify-center text-xs">
            ☺
          </span>
          <span className="leading-tight">
            <span className="block text-fg">未登录</span>
            <span className="block text-[11px]">点击登录，保存到自己的账号</span>
          </span>
        </button>
      </div>
    )
  }

  return (
    <div ref={boxRef} className="mt-auto px-4 py-4 border-t border-line relative">
      <button
        onClick={() => setMenuOpen((v) => !v)}
        className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-left hover:bg-[var(--brand-soft)] transition-colors"
      >
        <span className="w-7 h-7 shrink-0 rounded-full bg-[var(--brand)] text-white flex items-center justify-center text-xs">
          {initials(user)}
        </span>
        <span className="min-w-0 leading-tight">
          <span className="block text-sm text-fg truncate">{user.display_name}</span>
          <span className="block text-[11px] text-muted">
            {user.stats.cases} 个案件 · {user.stats.entries} 条经验
          </span>
        </span>
      </button>

      {menuOpen && (
        <div className="absolute left-4 right-4 bottom-[3.75rem] bg-canvas border border-line rounded-md shadow-lg py-1 z-30">
          <div className="px-3 py-1.5 text-[11px] text-muted border-b border-line truncate">
            {user.email}
          </div>
          <button
            onClick={() => {
              setMenuOpen(false)
              onLogout()
            }}
            className="w-full text-left px-3 py-1.5 text-sm text-fg hover:bg-[var(--brand-soft)]"
          >
            退出登录
          </button>
        </div>
      )}
    </div>
  )
}
