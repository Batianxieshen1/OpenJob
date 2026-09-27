import { useEffect, useState } from 'react'
import { Activity, AlertTriangle, ListChecks } from 'lucide-react'
import { cn } from '@/lib/utils'

interface RunRow {
  id: string
  status: string
  created_at: string
  finished_at?: string | null
  error?: string | null
  stop_reason?: string | null
  progress?: Record<string, unknown>
  options?: Record<string, unknown>
}

interface ErrorSummary {
  days: number
  total: number
  unclassified: number
  categories: Array<{ category: string; count: number; samples: Array<{ action: string; label: string }> }>
}

const RUN_STATUS_TONE: Record<string, string> = {
  completed: 'text-success border-success/30 bg-success/10',
  completed_with_errors: 'text-warning border-warning/30 bg-warning/10',
  failed: 'text-danger border-danger/30 bg-danger/10',
  stopped: 'text-muted border-card-border bg-card',
  interrupted: 'text-warning border-warning/30 bg-warning/10',
  paused: 'text-warning border-warning/30 bg-warning/10',
  running: 'text-primary border-primary/30 bg-accent-soft',
}

const SHORT_STATUS: Record<string, string> = {
  completed: '已完成',
  completed_with_shortage: '部分完成',
  completed_with_errors: '有错误',
  failed: '失败',
  stopped: '已停止',
  interrupted: '已中断',
  paused: '已暂停',
  running: '运行中',
}

function formatDuration(start?: string | null, end?: string | null) {
  if (!start || !end) return '—'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)} 秒`
  return `${Math.round(ms / 60_000)} 分钟`
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return time.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

function RunTable({ title, rows }: { title: string; rows: RunRow[] }) {
  return (
    <div className="rounded-2xl border border-card-border bg-surface-hover p-4">
      <div className="text-sm font-semibold">{title}</div>
      {rows.length === 0 ? (
        <p className="mt-2 text-xs text-muted">暂无记录</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {rows.slice(0, 8).map(row => (
            <li key={row.id} className="flex items-center justify-between gap-2 text-xs">
              <div className="min-w-0">
                <div className="font-semibold tabular-nums text-foreground">{formatTime(row.created_at)}</div>
                <div className="text-[11px] text-muted tabular-nums">耗时 {formatDuration(row.created_at, row.finished_at)}</div>
              </div>
              <span className={cn('shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold', RUN_STATUS_TONE[row.status] || 'text-muted border-card-border')}>
                {SHORT_STATUS[row.status] || row.status}
              </span>
              {(row.error || row.stop_reason) && (
                <span className="hidden max-w-[150px] truncate text-warning sm:block" title={row.error || row.stop_reason || ''}>
                  {row.error || row.stop_reason}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** C2 可观测性：运行历史（采集/评分/计划）+ 错误归因聚合，任何失败三次点击内可定位 */
export function RunsPanel() {
  const [scoringRuns, setScoringRuns] = useState<RunRow[]>([])
  const [collectionRuns, setCollectionRuns] = useState<RunRow[]>([])
  const [errors, setErrors] = useState<ErrorSummary | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      fetch('/api/scoring/runs', { cache: 'no-store' }).then(res => res.json()).catch(() => []),
      fetch('/api/collection/runs?limit=8', { cache: 'no-store' }).then(res => res.json()).catch(() => []),
      fetch('/api/errors/summary?days=30', { cache: 'no-store' }).then(res => res.json()).catch(() => null),
    ]).then(([scoring, collection, errorSummary]) => {
      if (cancelled) return
      setScoringRuns(Array.isArray(scoring) ? scoring : [])
      setCollectionRuns(Array.isArray(collection) ? collection : [])
      setErrors(errorSummary?.success ? (errorSummary.data as ErrorSummary) : null)
      setLoaded(true)
    })
    return () => { cancelled = true }
  }, [])

  if (!loaded) return <div className="rounded-module skeleton h-40" />

  return (
    <section className="rounded-module border border-card-border bg-card p-5">
      <div className="flex items-center gap-2">
        <ListChecks className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">运行记录（最近 30 天）</h2>
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <RunTable title="AI 评分运行" rows={scoringRuns} />
        <RunTable title="采集运行" rows={collectionRuns} />
        <div className="rounded-2xl border border-card-border bg-surface-hover p-4">
          <div className="flex items-center gap-1.5 text-sm font-semibold">
            <AlertTriangle className={cn('h-4 w-4', errors && errors.total > 0 ? 'text-warning' : 'text-success')} />
            错误归因
          </div>
          {!errors || errors.total === 0 ? (
            <p className="mt-2 text-xs text-success">近 30 天无失败记录。</p>
          ) : (
            <ul className="mt-2 space-y-1.5">
              {errors.categories.slice(0, 5).map(item => (
                <li key={item.category} className="text-xs">
                  <span className="font-semibold text-foreground tabular-nums">{item.count}</span>
                  <span className="ml-1.5 text-muted">{item.category.split(':')[1] || item.category}</span>
                  {item.samples[0] && (
                    <span className="ml-1 text-muted-3" title={item.samples[0].label}>（如：{item.samples[0].label.slice(0, 18)}）</span>
                  )}
                </li>
              ))}
              {errors.unclassified > 0 && (
                <li className="text-xs text-muted-3">另有 {errors.unclassified} 条未归类</li>
              )}
            </ul>
          )}
        </div>
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
        <Activity className="h-3.5 w-3.5" />
        每次任务的完整日志按任务 ID 持久化在 data/logs/，可通过 /api/logs/&lt;task_id&gt;/tail 查看尾部 200 行。
      </p>
    </section>
  )
}
