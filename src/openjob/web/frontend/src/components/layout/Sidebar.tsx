import { NavLink } from 'react-router-dom'
import { BarChart3, BriefcaseBusiness, ClipboardCheck, FileText, LayoutDashboard, Radar, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位池' },
  { to: '/confirm', icon: ClipboardCheck, label: '投递确认' },
  { to: '/resume', icon: FileText, label: '简历工作台' },
  { to: '/stats', icon: BarChart3, label: '市场分析' },
  { to: '/monitor', icon: Radar, label: '监测执行' },
  { to: '/config', icon: Settings, label: '配置' },
]


interface SidebarProps {
  pendingReplies?: number
}

export function Sidebar({ pendingReplies: pendingRepliesProp }: SidebarProps) {
  const [pendingReplies, setPendingReplies] = useState(pendingRepliesProp ?? 0)

  useEffect(() => {
    if (pendingRepliesProp !== undefined) {
      setPendingReplies(pendingRepliesProp)
      return
    }

    const fetchPendingReplies = async () => {
      try {
        const res = await fetch('/api/history/unresolved-replies/count')
        const data = await res.json()
        setPendingReplies(Number(data.count) || 0)
      } catch {
        setPendingReplies(0)
      }
    }

    fetchPendingReplies()
    const interval = setInterval(fetchPendingReplies, 30000)
    return () => clearInterval(interval)
  }, [pendingRepliesProp])

  return (
    <aside className="hidden w-[76px] shrink-0 flex-col items-center border-r border-card-border bg-shell py-5 lg:flex">
      {/* Logo */}
      <NavLink
        to="/"
        aria-label="OpenJob 工作台"
        className="mb-6 flex h-11 w-11 items-center justify-center rounded-2xl bg-ink text-shell shadow-pop"
      >
        <span className="text-sm font-bold tracking-tight">OJ</span>
      </NavLink>

      {/* 图标导航 */}
      <nav className="flex flex-1 flex-col items-center gap-1.5">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `group relative flex h-11 w-11 items-center justify-center rounded-2xl transition-soft ${
                isActive
                  ? 'bg-primary text-white shadow-pop'
                  : 'text-muted hover:bg-surface-hover hover:text-foreground'
              }`
            }
          >
            <item.icon className="h-[18px] w-[18px]" strokeWidth={1.8} />
            {/* Tooltip */}
            <span
              role="tooltip"
              className="pointer-events-none absolute left-full z-50 ml-2.5 whitespace-nowrap rounded-lg bg-ink px-2.5 py-1.5 text-xs text-shell opacity-0 shadow-pop transition-soft group-hover:translate-x-0 group-hover:opacity-100 -translate-x-1"
            >
              {item.label}
            </span>
            {item.to === '/monitor' && pendingReplies > 0 && (
              <span
                className="absolute right-2 top-2 h-2 w-2 rounded-full bg-danger ring-2 ring-shell"
                aria-label="有待处理事项"
              />
            )}
          </NavLink>
        ))}
      </nav>

      {/* 底部版本标识 */}
      <div className="mt-6 text-[10px] font-semibold text-muted-3" title="OpenJob v1.0">
        v1.0
      </div>
    </aside>
  )
}
