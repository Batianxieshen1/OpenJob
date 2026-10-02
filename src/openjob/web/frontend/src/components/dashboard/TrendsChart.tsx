import { useEffect, useMemo, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

interface TrendsPoint {
  label: string
  scraped: number
  scored: number
  sent: number
  isToday: boolean
}

interface TrendsRow {
  day: string
  scraped: number
  scored: number
  sent: number
}

function toPoints(rows: TrendsRow[]): TrendsPoint[] {
  const today = new Date()
  return (Array.isArray(rows) ? rows : []).map(row => {
    const parts = String(row.day || '').split('-').map(Number)
    const [year, month, day] = parts
    const isToday = year === today.getFullYear() && month === today.getMonth() + 1 && day === today.getDate()
    return {
      label: `${month}/${day}`,
      scraped: Number(row.scraped) || 0,
      scored: Number(row.scored) || 0,
      sent: Number(row.sent) || 0,
      isToday,
    }
  })
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

/** 求职趋势：近 7 日采集/评分/投递三条曲线（/api/trends 服务端按本地日聚合） */
export function TrendsChart() {
  const [rows, setRows] = useState<TrendsRow[]>([])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch('/api/trends')
        const data = await res.json()
        if (!cancelled) setRows(Array.isArray(data) ? data : [])
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

  const data = useMemo(() => toPoints(rows), [rows])

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
