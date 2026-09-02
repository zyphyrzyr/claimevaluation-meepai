/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 既有色板（向后兼容，未迁移页面仍可用）
        ink: {
          DEFAULT: '#0d1429',
          light: '#1a2540',
          pale: '#f5f6f8',
        },
        ember: {
          DEFAULT: '#d65938',
          dark: '#b84a2e',
          pale: '#fbe9e2',
        },
        // P2 Token 基座：双主题统一语义色（指向 CSS 变量，见 index.css）
        // 评估/案件 = 明场 A；模拟法庭 = 暗场剧场（[data-theme="theater"] 切换）
        canvas: 'var(--bg)',
        surface: 'var(--surface)',
        fg: 'var(--text)',
        muted: 'var(--text-muted)',
        line: 'var(--border)',
        brand: 'var(--brand)',
        brand2: 'var(--brand-2)',
        success: 'var(--success)',
        warning: 'var(--warning)',
        danger: 'var(--danger)',
        info: 'var(--info)',
      },
      fontFamily: {
        sans: ['-apple-system', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
