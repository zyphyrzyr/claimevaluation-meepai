import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { ThemeName } from './tokens'

interface ThemeCtxValue {
  theme: ThemeName
  setTheme: (t: ThemeName) => void
}

const ThemeCtx = createContext<ThemeCtxValue>({ theme: 'light', setTheme: () => {} })

/**
 * 双主题 Provider（P2）
 * 评估/案件默认明场 A；模拟法庭切换 data-theme="theater" 进入暗色剧场。
 * 变量定义在 index.css，组件统一走 token，切换只改 <html data-theme>。
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ThemeName>('light')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  return <ThemeCtx.Provider value={{ theme, setTheme }}>{children}</ThemeCtx.Provider>
}

export function useTheme() {
  return useContext(ThemeCtx)
}
