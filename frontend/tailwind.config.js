/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
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
      },
      fontFamily: {
        sans: ['-apple-system', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
