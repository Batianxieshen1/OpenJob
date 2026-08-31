import { useLocation } from 'react-router-dom'
import { Activity, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

const pageTitles: Record<string, string> = {
  '/': '工作台',
  '/jobs': '岗位池',
  '/confirm': '投递确认',
  '/resume': '简历工作台',
  '/monitor': '监测执行',
  '/config': '配置',
}

type Theme = 'light' | 'dark'

const SEND_WINDOW_START = 9 // 与 config.yaml throttle.send_windows 09:00-16:00 对应
const SEND_WINDOW_END = 16

function resolveInitialTheme(): Theme {
  const stored = localStorage.getItem('openjob-theme')
  if (stored === 'light' || stored === 'dark') return stored
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.classList.toggle('light', theme === 'light')
}

function formatClock(now: Date): string {
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const ss = String(now.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

function withinSendWindow(now: Date): boolean {
  const hour = now.getHours()
  return hour >= SEND_WINDOW_START && hour < SEND_WINDOW_END
}

export function Header() {
  const location = useLocation()
  const title = pageTitles[location.pathname] || 'OpenJob'
  const [theme, setTheme] = useState<Theme>(resolveInitialTheme)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    applyTheme(theme)
    localStorage.setItem('openjob-theme', theme)
  }, [theme])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const toggleTheme = () => setTheme(prev => (prev === 'dark' ? 'light' : 'dark'))
  const inWindow = withinSendWindow(now)

  return (
    <header className="h-16 border-b border-card-border bg-surface-hover flex items-center justify-between px-6">
      <h1 className="text-lg font-black text-foreground">{title}</h1>
      <div className="flex items-center gap-3 text-xs text-muted">
        <span
          className="font-mono tabular-nums text-foreground"
          title={inWindow ? '发送窗口内（09:00-16:00）' : '发送窗口外（09:00-16:00），投递任务会暂停'}
        >
          <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${inWindow ? 'bg-success' : 'bg-muted'}`} />
          {formatClock(now)}
        </span>
        <div className="flex items-center gap-2">
          <Activity className="w-3 h-3 text-success" />
          <span>本地服务运行中</span>
        </div>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          className="flex h-8 w-8 items-center justify-center rounded-control border border-card-border bg-card text-muted transition-soft hover:text-foreground hover:bg-surface-hover active:scale-95"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </header>
  )
}
