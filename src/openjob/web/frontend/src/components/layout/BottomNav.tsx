import { NavLink } from 'react-router-dom'
import { BarChart3, BriefcaseBusiness, ClipboardCheck, FileText, LayoutDashboard, Radar, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'

const items = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位池' },
  { to: '/confirm', icon: ClipboardCheck, label: '确认' },
  { to: '/resume', icon: FileText, label: '简历' },
  { to: '/monitor', icon: Radar, label: '监测' },
  { to: '/config', icon: Settings, label: '配置' },
]

/** 移动端底部导航（<lg 生效；桌面由左侧图标栏承担） */
export function BottomNav() {
  return (
    <nav
      aria-label="移动端导航"
      className="fixed inset-x-0 bottom-0 z-40 flex items-stretch justify-around border-t border-card-border bg-shell/95 backdrop-blur lg:hidden"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      {items.map(item => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              'flex flex-1 flex-col items-center gap-0.5 px-1 py-2 text-[10px] transition-soft',
              isActive ? 'text-primary' : 'text-muted'
            )
          }
        >
          {({ isActive }) => (
            <>
              <item.icon className="h-5 w-5" strokeWidth={isActive ? 2.2 : 1.8} />
              {item.label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}
