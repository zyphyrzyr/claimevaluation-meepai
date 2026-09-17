import { useEffect, useRef, useState } from 'react'
import type { AuthUser } from '../api'

/**
 * 左侧导航栏左下角的登录状态块，起点定在**侧栏高度的 3/4 处**。
 *
 * 位置是调过三版的：
 * - 贴底（mt-auto）：落到屏幕最下面一行，跟导航隔了约 355px，像另一块内容；
 * - 紧贴导航项下方：太靠上，像导航的第三项，压住了导航本身的层级；
 * - 现在：top-[75vh]，底部还留着约 25vh 的空白，既不贴底也不跟导航抢位置。
 *
 * 用绝对定位而不是 margin，是因为上方还有标题 + mt-[25vh] 的导航，
 * 高度会随窗口变；写死「距离顶部 75vh」才稳定。aside 本身是 sticky，
 * 天然就是这块绝对定位的包含块，不用再给它加 relative（加了会顶掉 sticky）。
 *
 * 纯展示组件：不读 AuthContext，全部由 props 驱动——这样「未登录 / 已登录」
 * 两种形态都能被单独渲染出来做冒烟断言。
 *
 * 未登录时整块是个按钮（点它弹登录框）；已登录时点它展开一个小菜单
 * （只有「退出登录」一项）。菜单向下展开，不做成常驻的，是为了不抢导航的注意力。
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
      <div className="absolute left-0 right-0 top-[75vh] px-4">
        <div className="h-9 rounded-md bg-[var(--brand-soft)] animate-pulse" />
      </div>
    )
  }

  if (!user) {
    return (
      <div className="absolute left-0 right-0 top-[75vh] px-4">
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
    <div ref={boxRef} className="absolute left-0 right-0 top-[75vh] px-4">
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

      {/* 菜单向下展开：这一块已经不贴底了，再往上弹会跑到侧栏外 */}
      {menuOpen && (
        <div className="absolute left-4 right-4 top-[3.5rem] bg-canvas border border-line rounded-md shadow-lg py-1 z-30">
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
