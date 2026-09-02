import { NavLink, useLocation } from 'react-router-dom'
import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'

// 胶囊导航：四个主流程页面（其余页面从左侧图标栏进入）
const pillItems = [
  { to: '/', label: '工作台' },
  { to: '/jobs', label: '岗位池' },
  { to: '/confirm', label: '投递确认' },
  { to: '/resume', label: '简历工作台' },
]

// 不在胶囊组里的页面在标题位显示页面名
const pageTitles: Record<string, string> = {
  '/stats': '市场分析',
  '/monitor': '监测执行',
  '/config': '配置',
}

type Theme = 'light' | 'dark'

const SEND_WINDOW_START = 9 // 与 config.yaml throttle.send_windows 09:00-16:00 对应
const SEND_WINDOW_END = 16

function resolveInitialTheme(): Theme {
  // 支持 ?theme=light|dark 链接参数（测试与分享用），其次本地偏好，最后跟随系统
  const fromQuery = new URLSearchParams(window.location.search).get('theme')
  if (fromQuery === 'light' || fromQuery === 'dark') return fromQuery
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
  const inPills = pillItems.some(item => item.to === location.pathname)
  const fallbackTitle = pageTitles[location.pathname]
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
    <header className="flex h-[72px] shrink-0 items-center justify-between gap-4 border-b border-card-border px-5 md:px-7 xl:px-9">
      <div className="flex min-w-0 items-center gap-3">
        {inPills ? (
          <nav
            aria-label="主导航"
            className="-mx-1 flex max-w-full items-center gap-1.5 overflow-x-auto rounded-full border border-card-border bg-card p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {pillItems.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'shrink-0 whitespace-nowrap rounded-full px-4 py-1.5 text-[13px] font-semibold transition-soft',
                    isActive
                      ? 'bg-ink text-shell shadow-pop'
                      : 'text-muted hover:-translate-y-px hover:text-foreground'
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        ) : (
          <h1 className="truncate text-lg font-semibold tracking-tight text-foreground">
            {fallbackTitle || 'OpenJob'}
          </h1>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2.5 text-xs text-muted">
        <div className="hidden items-center gap-1.5 md:flex" title="本地服务状态">
          <span className="breathe inline-block h-1.5 w-1.5 rounded-full bg-success" />
          <span>本地服务</span>
        </div>
        <span
          className="hidden font-mono tabular-nums text-foreground sm:inline"
          title={inWindow ? '发送窗口内（09:00-16:00）' : '发送窗口外（09:00-16:00），投递任务会暂停'}
        >
          <span className={cn('mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle', inWindow ? 'bg-success' : 'bg-muted-3')} />
          {formatClock(now)}
        </span>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-card-border bg-card text-muted transition-soft hover:-translate-y-px hover:text-foreground active:scale-95"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </header>
  )
}
