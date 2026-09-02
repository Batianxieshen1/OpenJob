import { useEffect, useState } from 'react'

interface UsageDay {
  date: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  estimated_cost: number
}

interface UsageSummary {
  days: UsageDay[]
  total: { calls: number; prompt_tokens: number; completion_tokens: number; estimated_cost: number }
}

const RADIUS = 52
const CIRC = 2 * Math.PI * RADIUS

function fmtTokens(n: number) {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : n.toLocaleString()
}

/** AI 用量环形卡：今日调用占近 7 日峰值的比例 + 费用摘要 */
export function UsageDonutCard() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)

  useEffect(() => {
    fetch('/api/usage/summary?days=7')
      .then(res => res.json())
      .then(setSummary)
      .catch(() => {})
  }, [])

  if (!summary || summary.total.calls === 0) return null

  const today = summary.days[0]
  const peak = Math.max(...summary.days.map(d => d.calls), 1)
  const pct = Math.min(100, Math.round(((today?.calls || 0) / peak) * 100))
  const todayTokens = (today?.prompt_tokens || 0) + (today?.completion_tokens || 0)

  return (
    <section className="flex min-h-[196px] items-center gap-5 rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="relative shrink-0" role="img" aria-label={`今日 AI 调用 ${today?.calls || 0} 次，占近 7 日峰值 ${pct}%`}>
        <svg width="128" height="128" viewBox="0 0 128 128">
          <circle cx="64" cy="64" r={RADIUS} fill="none" stroke="rgb(var(--surface-hover))" strokeWidth="10" />
          <circle
            cx="64"
            cy="64"
            r={RADIUS}
            fill="none"
            stroke="rgb(var(--accent))"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={CIRC}
            strokeDashoffset={CIRC * (1 - pct / 100)}
            transform="rotate(-90 64 64)"
            style={{ transition: 'stroke-dashoffset 700ms cubic-bezier(0.22,1,0.36,1)' }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold tabular-nums text-foreground">{today?.calls || 0}</span>
          <span className="text-[10px] text-muted">今日调用</span>
        </div>
      </div>
      <div className="min-w-0">
        <h3 className="text-[15px] font-semibold">AI 用量</h3>
        <ul className="mt-2 space-y-1 text-xs text-muted">
          <li className="flex justify-between gap-3"><span>今日 tokens</span><span className="font-semibold text-foreground tabular-nums">{fmtTokens(todayTokens)}</span></li>
          <li className="flex justify-between gap-3"><span>近 7 日调用</span><span className="font-semibold text-foreground tabular-nums">{summary.total.calls} 次</span></li>
          <li className="flex justify-between gap-3"><span>近 7 日费用</span><span className="font-semibold text-foreground tabular-nums">¥{summary.total.estimated_cost.toFixed(2)}</span></li>
        </ul>
      </div>
    </section>
  )
}
