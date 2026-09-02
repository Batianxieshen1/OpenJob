import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

interface TrendsPoint {
  label: string
  scraped: number
  scored: number
  sent: number
  isToday: boolean
}

const SCRAPE_ACTIONS = new Set(['scrape'])
const SCORE_ACTIONS = new Set(['scored', 'filtered'])
const SEND_ACTIONS = new Set(['sent', 'manual_sent', 'resume_sent'])

function buildSeries(records: Array<{ action: string; created_at: string }>): TrendsPoint[] {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const buckets: TrendsPoint[] = []
  for (let offset = 6; offset >= 0; offset -= 1) {
    const d = new Date(today)
    d.setDate(today.getDate() - offset)
    buckets.push({
      label: `${d.getMonth() + 1}/${d.getDate()}`,
      scraped: 0,
      scored: 0,
      sent: 0,
      isToday: offset === 0,
    })
  }
  const index = new Map(buckets.map((b, i) => [b.label, i]))
  for (const record of records) {
    const created = new Date(record.created_at.replace(' ', 'T'))
    if (Number.isNaN(created.getTime())) continue
    const i = index.get(`${created.getMonth() + 1}/${created.getDate()}`)
    if (i === undefined) continue
    if (SCRAPE_ACTIONS.has(record.action)) buckets[i].scraped += 1
    else if (SCORE_ACTIONS.has(record.action)) buckets[i].scored += 1
    else if (SEND_ACTIONS.has(record.action)) buckets[i].sent += 1
  }
  return buckets
}

function TrendTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-card-border bg-shell px-3 py-2 text-xs shadow-pop">
      <div className="mb-1 font-semibold text-foreground">{label}</div>
      {payload.map(item => (
        <div key={item.name} className="flex items-center gap-2 text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-primary" />
          {item.name}：<span className="font-semibold text-foreground tabular-nums">{item.value}</span>
        </div>
      ))}
    </div>
  )
}

/** 求职趋势：近 7 日采集/评分/投递三条曲线（本地 history 台账聚合） */
export function TrendsChart() {
  const [records, setRecords] = useState<Array<{ action: string; created_at: string }>>([])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch('/api/history?limit=300')
        const data = await res.json()
        const list = Array.isArray(data?.history) ? data.history : Array.isArray(data) ? data : []
        if (!cancelled) setRecords(list)
      } catch {
        /* 静默 */
      }
    }
    void load()
    const timer = window.setInterval(load, 120_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const data = useMemo(() => buildSeries(records), [records])

  const TodayTick = (props: { x?: number; y?: number; payload?: { value: string } }) => {
    const point = data.find(d => d.label === props.payload?.value)
    return (
      <text
        x={props.x}
        y={(props.y || 0) + 12}
        textAnchor="middle"
        fontSize={10}
        fontWeight={point?.isToday ? 700 : 400}
        fill={point?.isToday ? 'rgb(var(--accent))' : 'rgb(var(--text-3))'}
      >
        {props.payload?.value}
      </text>
    )
  }

  return (
    <section className="flex min-h-[196px] flex-col rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-semibold">近 7 日趋势</h3>
        <div className="flex items-center gap-3 text-[11px] text-muted">
          <span className="flex items-center gap-1"><span className="h-0.5 w-3 rounded-full bg-primary" />采集</span>
          <span className="flex items-center gap-1"><span className="h-0.5 w-3 rounded-full bg-primary/50" />评分</span>
          <span className="flex items-center gap-1"><span className="h-0.5 w-3 rounded-full bg-ink" />投递</span>
        </div>
      </div>
      <div className="mt-2 h-[128px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -22 }}>
            <CartesianGrid vertical={false} stroke="rgb(var(--border-c))" strokeDasharray="3 6" />
            <XAxis dataKey="label" axisLine={false} tickLine={false} tick={<TodayTick />} interval={0} />
            <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'rgb(var(--text-3))' }} allowDecimals={false} />
            <Tooltip cursor={{ stroke: 'rgb(var(--border-c))' }} content={<TrendTooltip />} />
            <Line type="monotone" dataKey="scraped" name="采集" stroke="rgb(var(--accent))" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="scored" name="评分" stroke="rgb(var(--accent) / 0.5)" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="sent" name="投递" stroke="rgb(var(--ink))" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
