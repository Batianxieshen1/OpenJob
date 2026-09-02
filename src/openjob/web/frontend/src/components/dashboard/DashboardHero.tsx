import { Radar, RefreshCw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface DashboardHeroProps {
  onRunFullFlow: () => void
  onOpenCollect: () => void
  onRunMonitor: () => void
  monitorRunning: boolean
  refreshing: boolean
  onRefresh: () => void
}

/** Hero 主视觉：eyebrow + 主标题 + 三个任务入口（全流程 / 单独采集 / 单独监测） */
export function DashboardHero({ onRunFullFlow, onOpenCollect, onRunMonitor, monitorRunning, refreshing, onRefresh }: DashboardHeroProps) {
  const today = new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })
  return (
    <section className="relative flex min-h-[212px] flex-col justify-between overflow-hidden rounded-module border border-card-border bg-card p-6 shadow-card">
      {/* 背景装饰：柔和蓝晕 + 细网格，克制不抢内容 */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-20 -top-24 h-64 w-64 rounded-full bg-accent-soft opacity-70 blur-2xl"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            'linear-gradient(rgb(var(--border-c)) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--border-c)) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
          maskImage: 'radial-gradient(420px 220px at 85% 0%, black, transparent)',
          WebkitMaskImage: 'radial-gradient(420px 220px at 85% 0%, black, transparent)',
        }}
      />

      <div className="relative">
        <div className="text-[11px] font-semibold tracking-[0.22em] text-primary">OPENJOB DAILY</div>
        <h2 className="mt-2 text-[28px] font-semibold leading-tight tracking-tight xl:text-[32px]">
          今天，让合适的岗位
          <br />
          更快找到你
        </h2>
        <p className="mt-2 max-w-md text-[13px] leading-6 text-muted">
          从采集、评分到投递确认，统一管理今天的求职行动。
        </p>
      </div>

      <div className="relative mt-5 flex items-center gap-2.5">
        <Button
          onClick={onRunFullFlow}
          className="h-11 rounded-full bg-ink px-6 text-[13px] font-semibold text-shell hover:bg-ink/85"
        >
          运行全流程
        </Button>
        <button
          type="button"
          onClick={onOpenCollect}
          aria-label="单独采集岗位"
          title="单独采集岗位"
          className="h-11 whitespace-nowrap items-center gap-1.5 rounded-full border border-card-border bg-card px-4 text-[13px] font-semibold text-foreground transition-soft hover:-translate-y-px hover:border-primary/50 hover:text-primary active:scale-95 sm:px-5 inline-flex"
        >
          <Search className="h-4 w-4" />
          <span className="hidden sm:inline">单独采集</span>
        </button>
        <button
          type="button"
          onClick={onRunMonitor}
          aria-label={monitorRunning ? '监测运行中' : '开启监测'}
          title={monitorRunning ? '监测运行中' : '开启监测'}
          className="h-11 whitespace-nowrap items-center gap-1.5 rounded-full border border-card-border bg-card px-4 text-[13px] font-semibold text-foreground transition-soft hover:-translate-y-px hover:border-primary/50 hover:text-primary active:scale-95 sm:px-5 inline-flex"
        >
          <Radar className={monitorRunning ? 'h-4 w-4 text-success' : 'h-4 w-4'} />
          <span className="hidden sm:inline">{monitorRunning ? '监测运行中' : '开启监测'}</span>
        </button>
        <button
          type="button"
          onClick={onRefresh}
          aria-label="刷新数据"
          title="刷新数据"
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-full text-muted transition-soft hover:bg-surface-hover hover:text-foreground"
        >
          <RefreshCw className={refreshing ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
        </button>
        <span className="hidden whitespace-nowrap text-xs text-muted-3 md:inline" title="按浏览器本地时区显示">{today}</span>
      </div>
    </section>
  )
}
