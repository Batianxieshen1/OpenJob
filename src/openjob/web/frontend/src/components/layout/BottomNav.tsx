import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { BarChart3, BriefcaseBusiness, ClipboardCheck, FileText, Inbox, LayoutDashboard, MoreHorizontal, Radar, Settings } from 'lucide-react'
import { cn } from '@/lib/utils'

/** P1-5：一级入口收敛为 4 个 + 更多面板；监测/回复/统计/配置收进「更多」。触控目标 ≥56px。 */
const primaryItems = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位' },
  { to: '/confirm', icon: ClipboardCheck, label: '确认' },
  { to: '/resume', icon: FileText, label: '简历' },
]

const moreItems = [
  { to: '/monitor', icon: Radar, label: '监测' },
  { to: '/inbox', icon: Inbox, label: '回复' },
  { to: '/stats', icon: BarChart3, label: '统计' },
  { to: '/config', icon: Settings, label: '配置' },
]

/** 移动端底部导航（<lg 生效；桌面由左侧图标栏承担） */
export function BottomNav() {
  const [moreOpen, setMoreOpen] = useState(false)
  const moreActive = moreItems.some(item => window.location.pathname.startsWith(item.to))

  return (
    <>
      {moreOpen && (
        <div className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm lg:hidden" onMouseDown={() => setMoreOpen(false)}>
          <div
            className="absolute inset-x-0 bottom-0 rounded-t-3xl border-t border-card-border bg-card p-5 pb-[calc(1.5rem+env(safe-area-inset-bottom))] shadow-2xl"
            onMouseDown={event => event.stopPropagation()}
          >
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-card-border" aria-hidden />
            <div className="grid grid-cols-4 gap-2">
              {moreItems.map(item => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={() => setMoreOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      'flex min-h-[72px] flex-col items-center justify-center gap-1 rounded-2xl border text-xs font-medium transition-soft',
                      isActive
                        ? 'border-primary/40 bg-accent-soft text-primary'
                        : 'border-card-border bg-card text-muted',
                    )
                  }
                >
                  <item.icon className="h-5 w-5" strokeWidth={1.9} />
                  {item.label}
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}
      <nav
        aria-label="移动端导航"
        className="fixed inset-x-0 bottom-0 z-50 flex items-stretch justify-around border-t border-card-border bg-shell/95 backdrop-blur lg:hidden"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        {primaryItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                'flex min-h-[56px] flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium transition-soft',
                isActive ? 'text-primary' : 'text-muted',
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
        <button
          type="button"
          aria-label="更多页面"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(open => !open)}
          className={cn(
            'flex min-h-[56px] flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium transition-soft',
            moreActive ? 'text-primary' : 'text-muted',
          )}
        >
          <MoreHorizontal className="h-5 w-5" strokeWidth={moreActive ? 2.2 : 1.8} />
          更多
        </button>
      </nav>
    </>
  )
}
