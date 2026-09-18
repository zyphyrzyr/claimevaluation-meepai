import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { authApi, setUnauthorizedHandler, type AuthUser } from '../api'

/**
 * 登录态上下文。
 *
 * 设计要点：
 * - **会话走 httpOnly cookie**，前端不持有也不存储任何 token。所以这里没有
 *   localStorage、没有 Authorization 头，登出就是「让服务端那张票作废」。
 * - 首屏一定会调一次 /auth/me。未登录是**正常状态**（user=null），不是错误——
 *   后端刻意不返回 401，否则 401 拦截器会在每次开页面时弹一次登录框。
 * - 写操作撞 401 时由这里统一弹框，调用方不必各自处理。
 */

interface AuthCtxValue {
  /** 未登录为 null；首屏请求未回来时也是 null，配合 loading 区分 */
  user: AuthUser | null
  /** 首屏 /auth/me 是否已返回 */
  loading: boolean
  /** 登录框是否打开 */
  loginOpen: boolean
  /** 打开登录框。reason 会显示在框顶，说明「为什么要登录」 */
  openLogin: (reason?: string) => void
  closeLogin: () => void
  /** 为什么弹的框（例如「登录后才能新建案件」） */
  loginReason: string
  login: (email: string, password: string) => Promise<AuthUser>
  register: (email: string, password: string, displayName?: string) => Promise<AuthUser>
  logout: () => Promise<void>
  /** 登录成功后刷新统计（建案/沉淀之后数字会变） */
  refresh: () => Promise<void>
}

const AuthCtx = createContext<AuthCtxValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginReason, setLoginReason] = useState('')

  const refresh = useCallback(async () => {
    try {
      const { user: u } = await authApi.me()
      setUser(u)
    } catch {
      // 取不到就当未登录：首屏宁可让用户以匿名身份继续浏览，
      // 也不要因为一个探测请求失败就白屏。
      setUser(null)
    }
  }, [])

  useEffect(() => {
    let alive = true
    authApi
      .me()
      .then(({ user: u }) => alive && setUser(u))
      .catch(() => alive && setUser(null))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  // 任意写操作撞 401 → 弹框并说明原因
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setLoginReason('这个操作需要登录')
      setLoginOpen(true)
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const openLogin = useCallback((reason?: string) => {
    setLoginReason(reason ?? '')
    setLoginOpen(true)
  }, [])

  const closeLogin = useCallback(() => setLoginOpen(false), [])

  const login = useCallback(
    async (email: string, password: string) => {
      const { user: u } = await authApi.login(email, password)
      setUser(u)
      return u
    },
    [],
  )

  const register = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const { user: u } = await authApi.register(email, password, displayName)
      setUser(u)
      return u
    },
    [],
  )

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      // 即便请求失败也要清掉本地状态：用户点的是「退出」，
      // 界面上还挂着别人的名字比请求失败更糟。
      setUser(null)
    }
  }, [])

  const value = useMemo<AuthCtxValue>(
    () => ({
      user, loading, loginOpen, loginReason,
      openLogin, closeLogin, login, register, logout, refresh,
    }),
    [user, loading, loginOpen, loginReason, openLogin, closeLogin, login, register, logout, refresh],
  )

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export function useAuth(): AuthCtxValue {
  const ctx = useContext(AuthCtx)
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用')
  return ctx
}
