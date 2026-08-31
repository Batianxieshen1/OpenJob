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
        background: 'rgb(var(--bg) / <alpha-value>)',
        foreground: 'rgb(var(--text) / <alpha-value>)',
        card: 'rgb(var(--surface) / <alpha-value>)',
        'card-border': 'rgb(var(--border-c) / <alpha-value>)',
        'surface-hover': 'rgb(var(--surface-hover) / <alpha-value>)',
        'accent-soft': 'rgb(var(--accent-soft) / 0.10)',
        primary: 'rgb(var(--accent) / <alpha-value>)',
        success: 'rgb(var(--success) / <alpha-value>)',
        warning: 'rgb(var(--warning) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
        muted: 'rgb(var(--text-2) / <alpha-value>)',
      },
      borderRadius: {
        control: 'var(--radius-control)',
        card: 'var(--radius-card)',
        overlay: 'var(--radius-overlay)',
      },
    },
  },
  plugins: [],
}
