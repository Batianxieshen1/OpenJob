import { CalendarClock, CheckCircle2, ChevronRight, CirclePause, Clock3, ShieldCheck, XCircle } from 'lucide-react'
import type { ScheduledCollectionSummary } from '@/hooks/useDashboard'

function formatDateTime(value: string | null) {
  if (!value) return '尚无下一次计划'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.replace('T', ' ')
  return date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

function statusText(status?: string) {
  if (status === 'completed') return '已完成'
  if (status === 'running') return '执行中'
  if (status === 'skipped') return '已跳过'
  if (status === 'failed') return '执行失败'
  if (status === 'stopped') return '已停止'
  return '暂无记录'
}

export function ScheduledCollectionCard({ schedule }: { schedule: ScheduledCollectionSummary }) {
  const last = schedule.last_run
  const paused = schedule.pause_today
  const statusTone = last?.status === 'completed'
    ? 'text-success bg-success/10'
    : last?.status === 'skipped' || paused
      ? 'text-warning bg-warning/10'
      : last?.status === 'failed'
        ? 'text-danger bg-danger/10'
        : 'text-primary bg-accent-soft'

  return (
    <section className="flex min-h-[212px] flex-col rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <CalendarClock className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0">
            <h3 className="text-[13px] font-semibold text-foreground">计划任务</h3>
            <p className="mt-0.5 truncate text-[11px] text-muted">
              {schedule.enabled ? (paused ? '今日已暂停' : '仅采集 + AI 评分') : '尚未启用'}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => { window.location.href = '/config?section=collection_schedule' }}
          className="flex shrink-0 items-center gap-0.5 rounded-full px-2 py-1 text-[11px] font-semibold text-primary transition-soft hover:bg-accent-soft"
        >
          设置 <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-4 rounded-2xl bg-surface-hover px-3.5 py-3">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          {paused ? <CirclePause className="h-3.5 w-3.5 text-warning" /> : <Clock3 className="h-3.5 w-3.5 text-primary" />}
          {paused ? '明日会自动恢复' : '下一次执行'}
        </div>
        <div className="mt-1.5 text-base font-semibold tabular-nums text-foreground">
          {schedule.enabled && !paused ? formatDateTime(schedule.next_run_at) : (paused ? '今日已暂停' : '请先设置时间')}
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl border border-card-border px-2 py-2">
          <div className="text-[10px] text-muted">今日完成</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{schedule.today_executed}</div>
        </div>
        <div className="rounded-xl border border-card-border px-2 py-2">
          <div className="text-[10px] text-muted">最近新增</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{last?.new_jobs_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-card-border px-2 py-2">
          <div className="text-[10px] text-muted">最近高分</div>
          <div className="mt-0.5 text-sm font-semibold tabular-nums">{last?.high_score_count ?? 0}</div>
        </div>
      </div>

      <div className="mt-auto flex items-center gap-2 pt-3 text-[11px] text-muted">
        {last?.status === 'completed' ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" /> : last?.status === 'failed' ? <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" /> : <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-primary" />}
        <span className={`shrink-0 rounded-full px-2 py-0.5 font-semibold ${statusTone}`}>{statusText(last?.status)}</span>
        <span className="truncate" title={last?.reason || '不会自动发送招呼语、简历或回复'}>{last?.reason || '不会自动发送任何内容'}</span>
      </div>
    </section>
  )
}
