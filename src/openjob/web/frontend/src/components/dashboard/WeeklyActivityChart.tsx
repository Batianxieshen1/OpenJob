import { useEffect, useMemo, useState } from 'react'
import { ResponsiveContainer, BarChart, Bar, XAxis, Tooltip } from 'recharts'

interface DayPoint {
  day: string // 周一…周日
  date: string // M/D
  delivers: number
  resumes: number
  isToday: boolean
}

const SEND_ACTIONS = new Set(['sent', 'manual_sent'])
const RESUME_ACTIONS = new Set(['resume_sent', 'needs_resume'])

function buildLast7Days(records: Array<{ action: string; created_at: string }>): DayPoint[] {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const buckets: DayPoint[] = []
  for (let offset = 6; offset >= 0; offset -= 1) {
    const d = new Date(today)
    d.setDate(today.getDate() - offset)
    buckets.push({
      day: d.toLocaleDateString('zh-CN', { weekday: 'short' }).replace('周', '周'),
      date: `${d.getMonth() + 1}/${d.getDate()}`,
      delivers: 0,
      resumes: 0,
      isToday: offset === 0,
    })
  }
  const index = new Map<string, number>()
  buckets.forEach((b, i) => index.set(b.date, i))

  for (const record of records) {
    const created = new Date(record.created_at.replace(' ', 'T'))
    if (Number.isNaN(created.getTime())) continue
    const key = `${created.getMonth() + 1}/${created.getDate()}`
    const i = index.get(key)
    if (i === undefined) continue
    if (SEND_ACTIONS.has(record.action)) buckets[i].delivers += 1
    else if (RESUME_ACTIONS.has(record.action)) buckets[i].resumes += 1
  }
  return buckets
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: Array<{ name: string; value: number }> }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-card-border bg-shell px-3 py-2 text-xs shadow-pop">
      {payload.map(item => (
        <div key={item.name} className="flex items-center gap-2 text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-primary" />
          {item.name}：<span className="font-semibold text-foreground tabular-nums">{item.value}</span>
        </div>
      ))}
    </div>
  )
}

/** 近 7 日求职状态：深浅蓝柱状（数据来自本地 history 台账） */
export function WeeklyActivityChart() {
  const [records, setRecords] = useState<Array<{ action: string; created_at: string }>>([])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch('/api/history?limit=300')
        const data = await res.json()
        if (!cancelled && Array.isArray(data?.history)) setRecords(data.history)
        else if (!cancelled && Array.isArray(data)) setRecords(data)
      } catch {
        /* 静默：图表保持空态 */
      }
    }
    void load()
    const timer = window.setInterval(load, 120_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const data = useMemo(() => buildLast7Days(records), [records])
  const total = data.reduce((sum, d) => sum + d.delivers + d.resumes, 0)

  const TodayTick = (props: { x?: number; y?: number; payload?: { value: string } }) => {
    const point = data.find(d => d.day === props.payload?.value)
    const isToday = point?.isToday
    return (
      <text
        x={props.x}
        y={(props.y || 0) + 12}
        textAnchor="middle"
        fontSize={10}
        fontWeight={isToday ? 700 : 400}
        fill={isToday ? 'rgb(var(--accent))' : 'rgb(var(--text-3))'}
      >
        {props.payload?.value}
      </text>
    )
  }

  return (
    <section className="flex min-h-[212px] flex-col rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="flex items-start justify-between">
        <h3 className="text-[15px] font-semibold">近 7 日行动</h3>
        <span className="rounded-full bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-primary tabular-nums">
          {total} 次
        </span>
      </div>
      <div className="mt-2 h-[142px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} barCategoryGap="32%" margin={{ top: 8, right: 0, bottom: 0, left: 0 }}>
            <XAxis dataKey="day" axisLine={false} tickLine={false} tick={<TodayTick />} interval={0} />
            <Tooltip cursor={{ fill: 'rgb(var(--surface-hover))', radius: 10 }} content={<ChartTooltip />} />
            <Bar dataKey="delivers" name="投递" stackId="a" fill="rgb(var(--accent))" radius={[0, 0, 4, 4]} />
            <Bar dataKey="resumes" name="简历" stackId="a" fill="rgb(var(--accent) / 0.35)" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
