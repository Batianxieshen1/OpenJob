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
  total: {
    calls: number
    prompt_tokens: number
    completion_tokens: number
    estimated_cost: number
  }
}

export function UsageCard() {
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    fetch('/api/usage/summary?days=7')
      .then(res => res.json())
      .then(setSummary)
      .catch(() => {})
  }, [])

  if (!summary || summary.total.calls === 0) return null

  const today = summary.days[0]
  const fmt = (n: number) => (n >= 10000 ? `${(n / 10000).toFixed(1)}万` : n.toLocaleString())

  return (
    <div className="rounded-card border border-card-border bg-card px-4 py-3 text-xs">
      <button
        type="button"
        onClick={() => setOpen(prev => !prev)}
        className="flex w-full items-center justify-between gap-2 text-left transition-soft hover:text-foreground"
      >
        <span className="font-bold text-foreground">AI 用量（近 7 天）</span>
        <span className="tabular-nums text-muted">
          {summary.total.calls} 次调用 · 预估 ¥{summary.total.estimated_cost.toFixed(2)}
        </span>
      </button>
      {today && (
        <p className="mt-1 text-muted">
          今日 {today.calls} 次 · 输入 {fmt(today.prompt_tokens)} / 输出 {fmt(today.completion_tokens)} tokens
        </p>
      )}
      {open && summary.days.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-card-border pt-2 text-muted">
          {summary.days.map(day => (
            <li key={day.date} className="flex items-center justify-between gap-2 tabular-nums">
              <span>{day.date.slice(5)}</span>
              <span>
                {day.calls} 次 · {fmt(day.prompt_tokens + day.completion_tokens)} tokens · ¥{day.estimated_cost.toFixed(2)}
              </span>
            </li>
          ))}
          <li className="pt-1 text-[10px]">费用为按输入 ¥1/百万、输出 ¥2/百万 token 的粗略估算，以 DeepSeek 账单为准。</li>
        </ul>
      )}
    </div>
  )
}
