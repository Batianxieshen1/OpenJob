import { ArrowUpRight, Play, Square } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { WorkbenchTask } from '@/hooks/useDashboard'

interface AutomationControlCardProps {
  activeTask: WorkbenchTask | null
  quota: { daily_limit: number; sent: number; remaining: number; exhausted: boolean }
  modePending: string | null
  onRunFullFlow: () => void
  onStopTask: () => void
}

const SEND_WINDOW = '09:00–16:00'

/** 今日自动化控制：日期 / 发送窗口 / 额度 / 启动停止（对应参考图右上控制卡） */
export function AutomationControlCard({ activeTask, quota, modePending, onRunFullFlow, onStopTask }: AutomationControlCardProps) {
  const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })
  const running = Boolean(activeTask)
  const usagePct = quota.daily_limit > 0 ? Math.min(100, Math.round((quota.sent / quota.daily_limit) * 100)) : 0

  return (
    <section className="flex min-h-[212px] flex-col rounded-module bg-ink p-5 text-shell shadow-pop dark:bg-[#1B2237] dark:text-white dark:shadow-[0_0_0_1px_rgb(96_130_255/0.25),0_16px_40px_rgb(0_0_0/0.45)]">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-medium tracking-[0.18em] text-shell/50 dark:text-white/50">今日自动化</div>
          <div className="mt-1 max-w-full text-[13px] font-semibold sm:text-sm">{today}</div>
        </div>
        <button
          type="button"
          onClick={() => { window.location.href = '/config?section=throttle' }}
          className="flex items-center gap-1 rounded-full border border-shell/15 dark:border-white/20 px-3 py-1.5 text-xs text-shell/80 dark:text-white/90 transition-soft hover:border-shell/40 hover:text-shell"
        >
          {SEND_WINDOW}
          <ArrowUpRight className="h-3 w-3" />
        </button>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2">
        <div className="rounded-2xl bg-shell/8 dark:bg-white/5 px-3 py-2.5">
          <div className="text-[11px] text-shell/50 dark:text-white/50">今日已投递</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums">
            {quota.sent}
            <span className="text-xs font-normal text-shell/50 dark:text-white/50"> / {quota.daily_limit}</span>
          </div>
        </div>
        <div className="rounded-2xl bg-shell/8 dark:bg-white/5 px-3 py-2.5">
          <div className="text-[11px] text-shell/50 dark:text-white/50">剩余额度</div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className="text-xl font-semibold tabular-nums">{quota.remaining}</span>
            <span className="text-[11px] text-shell/50 dark:text-white/50">已用 {usagePct}%</span>
          </div>
        </div>
      </div>

      {/* 额度进度条 */}
      <div className="mt-3 h-1 overflow-hidden rounded-full bg-shell/10 dark:bg-white/10">
        <div
          className={cn('h-full rounded-full transition-all duration-500', quota.exhausted ? 'bg-warning' : 'bg-primary')}
          style={{ width: `${usagePct}%` }}
        />
      </div>

      <div className="mt-auto pt-4">
        {running ? (
          <button
            type="button"
            onClick={onStopTask}
            disabled={modePending !== null || activeTask?.status === 'stopping'}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-full bg-warning/90 text-[13px] font-semibold text-white transition-soft hover:bg-warning disabled:opacity-60"
          >
            <Square className="h-3.5 w-3.5 fill-current" />
            {activeTask?.status === 'stopping' ? '停止中…' : `停止${activeTask?.label || '任务'}`}
          </button>
        ) : (
          <button
            type="button"
            onClick={onRunFullFlow}
            disabled={modePending !== null}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-full bg-primary text-[13px] font-semibold text-white shadow-pop transition-soft hover:-translate-y-px hover:bg-primary/90 disabled:opacity-60"
          >
            <Play className="h-3.5 w-3.5 fill-current" />
            {modePending === 'full' ? '任务启动中…' : '启动全流程任务'}
          </button>
        )}
      </div>
    </section>
  )
}
