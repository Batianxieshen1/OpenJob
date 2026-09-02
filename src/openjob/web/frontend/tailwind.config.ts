/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 全部指向 globals.css 的设计令牌，浅/深主题自动切换
        canvas: 'rgb(var(--canvas) / <alpha-value>)',      // 壳外画布
        shell: 'rgb(var(--shell) / <alpha-value>)',        // 悬浮应用壳
        ink: 'rgb(var(--ink) / <alpha-value>)',            // 黑胶囊（导航激活/主按钮）
        background: 'rgb(var(--bg) / <alpha-value>)',
        foreground: 'rgb(var(--text) / <alpha-value>)',
        card: 'rgb(var(--surface) / <alpha-value>)',
        'card-border': 'rgb(var(--border-c) / <alpha-value>)',
        'surface-hover': 'rgb(var(--surface-hover) / <alpha-value>)',
        'accent-soft': 'rgb(var(--accent-soft) / <alpha-value>)', // 整色浅蓝底
        primary: 'rgb(var(--accent) / <alpha-value>)',
        success: 'rgb(var(--success) / <alpha-value>)',
        warning: 'rgb(var(--warning) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
        muted: 'rgb(var(--text-2) / <alpha-value>)',
        'muted-3': 'rgb(var(--text-3) / <alpha-value>)',
      },
      borderRadius: {
        control: 'var(--radius-control)',
        card: 'var(--radius-card)',
        module: 'var(--radius-module)',
        overlay: 'var(--radius-overlay)',
        shell: 'var(--radius-shell)',
      },
      boxShadow: {
        shell: '0 24px 70px rgba(26, 32, 48, 0.10), 0 2px 8px rgba(26, 32, 48, 0.04)',
        card: '0 1px 2px rgba(26, 32, 48, 0.04), 0 8px 24px rgba(26, 32, 48, 0.05)',
        pop: '0 12px 32px rgba(26, 32, 48, 0.14)',
      },
      fontFamily: {
        sans: [
          'Inter',
          'HarmonyOS Sans SC',
          'PingFang SC',
          'Microsoft YaHei',
          'system-ui',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
