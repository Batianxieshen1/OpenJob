import { useCallback, useEffect, useState } from 'react'
import { MessageCircle, Send } from 'lucide-react'
import { parseUtc } from "@/lib/datetime"
import { cn } from '@/lib/utils'

interface DeliveryItem {
  id: number
  job_id: string
  action: 'sent' | 'error' | 'manual_sent' | 'send_blocked_fact_unverified' | 'send_blocked_fact_recheck' | 'send_blocked_invalid_url' | string
  detail: string
  created_at: string
  company: string
  title: string
  score: number
  job_status: string
  greeting: string | null
  greeting_fact_status: string | null
}

interface DeliveryLog {
  days: number
  summary: { sent: number; failed: number; blocked: number }
  items: DeliveryItem[]
}

const ACTION_META: Record<string, { label: string; tone: string }> = {
  sent: { label: '已发送', tone: 'text-success border-success/40 bg-success/10' },
  manual_sent: { label: '手动已发', tone: 'text-success border-success/40 bg-success/10' },
  error: { label: '发送失败', tone: 'text-danger border-danger/40 bg-danger/10' },
  send_blocked_fact_unverified: { label: '拦截：未验证', tone: 'text-warning border-warning/40 bg-warning/10' },
  send_blocked_fact_recheck: { label: '拦截：事实复检', tone: 'text-warning border-warning/40 bg-warning/10' },
  send_blocked_invalid_url: { label: '拦截：非平台链接', tone: 'text-warning border-warning/40 bg-warning/10' },
}

function formatTime(value: string) {
  const time = parseUtc(value)
  if (!time) return value
  return time.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

/** 投递记录：打招呼语发送进度 × 每条招呼语全文，一屏对照 */
export function DeliveryLogPanel() {
  const [log, setLog] = useState<DeliveryLog | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/delivery-log?days=7', { cache: 'no-store' })
      const payload = await res.json()
      setLog(payload?.success ? (payload.data as DeliveryLog) : null)
    } catch {
      setLog(null)
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (!loaded) return <div className="rounded-module skeleton h-48" />
  if (!log) return null

  return (
    <section className="rounded-module border border-card-border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Send className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">投递记录（近 7 天）</h2>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-success">已发 <span className="font-semibold tabular-nums">{log.summary.sent}</span></span>
          <span className="text-danger">失败 <span className="font-semibold tabular-nums">{log.summary.failed}</span></span>
          <span className="text-warning">拦截 <span className="font-semibold tabular-nums">{log.summary.blocked}</span></span>
          <button className="text-muted transition-soft hover:text-foreground" onClick={() => void load()}>刷新</button>
        </div>
      </div>
      {log.items.length === 0 ? (
        <p className="mt-3 text-xs text-muted">近 7 天没有投递动作。发送完成后，每条招呼语和结果会出现在这里。</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {log.items.map(item => {
            const meta = ACTION_META[item.action] || { label: item.action, tone: 'text-muted border-card-border bg-surface-hover' }
            const isOpen = expanded === item.id
            return (
              <li key={item.id} className="rounded-2xl border border-card-border bg-surface-hover p-3">
                <button
                  type="button"
                  className="flex w-full flex-wrap items-center justify-between gap-2 text-left"
                  onClick={() => setExpanded(isOpen ? null : item.id)}
                >
                  <span className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className={cn('shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold', meta.tone)}>{meta.label}</span>
                    <span className="truncate text-sm font-medium text-foreground">{item.company}｜{item.title}</span>
                    <span className="shrink-0 text-xs text-muted tabular-nums">{item.score} 分</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted tabular-nums">{formatTime(item.created_at)}</span>
                </button>
                {item.action === 'error' && item.detail && (
                  <p className="mt-1.5 text-xs text-danger">失败原因：{item.detail}</p>
                )}
                {item.action.startsWith('send_blocked') && item.detail && (
                  <p className="mt-1.5 text-xs text-warning">拦截原因：{item.detail}</p>
                )}
                {isOpen && (
                  <div className="mt-2 rounded-xl border border-card-border bg-card p-3">
                    <div className="flex items-center gap-1.5 text-xs font-semibold text-muted">
                      <MessageCircle className="h-3.5 w-3.5" />
                      招呼语全文{item.greeting_fact_status === 'verified' ? '（已过事实校验）' : ''}
                    </div>
                    <p className="mt-1.5 whitespace-pre-wrap text-sm leading-6 text-foreground">
                      {item.greeting || '（岗位未生成招呼语）'}
                    </p>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
