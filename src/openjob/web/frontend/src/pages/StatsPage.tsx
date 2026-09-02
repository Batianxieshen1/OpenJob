import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface NameCount {
  name: string
  count: number
}

interface MarketStats {
  total: number
  platform?: NameCount[]
  city?: NameCount[]
  salary?: NameCount[]
  education?: NameCount[]
  experience?: NameCount[]
  recruitment?: NameCount[]
  top_companies?: NameCount[]
  skill_freq?: NameCount[]
  welfare_freq?: NameCount[]
}

const RECRUITMENT_LABELS: Record<string, string> = {
  campus: '实习/校招',
  experienced: '正式/社招',
  unknown: '未识别',
}

const PLATFORM_LABELS: Record<string, string> = {
  boss: 'BOSS 直聘',
  zhilian: '智联招聘',
  '51job': '前程无忧',
}

function displayName(key: string, labels?: Record<string, string>): string {
  return labels?.[key] ?? key
}

function BarList({ items, labels, max, accent = false }: { items: NameCount[]; labels?: Record<string, string>; max?: number; accent?: boolean }) {
  const peak = max ?? Math.max(...items.map(i => i.count), 1)
  return (
    <ul className="space-y-1.5">
      {items.map(item => (
        <li key={item.name}>
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <span className="truncate text-foreground">{displayName(item.name, labels)}</span>
            <span className="shrink-0 tabular-nums text-muted">{item.count}</span>
          </div>
          <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-surface-hover">
            <div
              className={`h-full rounded-full transition-soft ${accent ? 'bg-primary' : 'bg-primary/60'}`}
              style={{ width: `${Math.max(4, Math.round((item.count / peak) * 100))}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

function StatCard({ title, items, labels, accent }: { title: string; items?: NameCount[]; labels?: Record<string, string>; accent?: boolean }) {
  if (!items || items.length === 0) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <BarList items={items} labels={labels} accent={accent} />
      </CardContent>
    </Card>
  )
}

type Scope = 'all' | 'pass' | 'filtered'

const SCOPE_LABELS: Record<Scope, string> = {
  all: '全部岗位',
  pass: '过线岗位',
  filtered: '已过滤岗位',
}

export default function StatsPage() {
  const [scope, setScope] = useState<Scope>('all')
  const [stats, setStats] = useState<MarketStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch(`/api/market/stats?scope=${scope}`)
      .then(res => res.json())
      .then(data => setStats(data))
      .catch(() => setError('市场数据加载失败，请稍后重试'))
      .finally(() => setLoading(false))
  }, [scope])

  if (loading) {
    return (
      <div className="rise-in mx-auto max-w-[1440px] space-y-4">
        <div className="skeleton h-8 w-48" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="skeleton h-56 w-full" />
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return <div className="rise-in rounded-card border border-danger/40 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</div>
  }

  if (!stats || stats.total === 0) {
    return (
      <div className="rise-in mx-auto max-w-[1440px]">
        <div className="rounded-card border border-dashed border-card-border px-6 py-16 text-center">
          <p className="text-sm text-muted">还没有岗位数据。先去工作台采集并评分，之后这里会展示市场画像。</p>
        </div>
      </div>
    )
  }

  return (
    <div className="rise-in mx-auto max-w-[1440px] space-y-4">
      <header className="space-y-3">
        <div>
          
          <p className="text-xs text-muted">
            基于{SCOPE_LABELS[scope]}（{stats.total} 个）的市场画像，随每轮采集自动更新。
          </p>
        </div>
        <div className="flex overflow-hidden rounded-md border border-card-border" role="group" aria-label="统计范围">
          {(Object.keys(SCOPE_LABELS) as Scope[]).map(key => (
            <button
              key={key}
              type="button"
              onClick={() => setScope(key)}
              className={`h-8 px-3 text-xs font-bold transition-soft ${
                scope === key ? 'bg-primary text-white' : 'bg-card text-muted hover:bg-surface-hover hover:text-foreground'
              }`}
            >
              {SCOPE_LABELS[key]}
            </button>
          ))}
        </div>
      </header>

      <div className="stagger grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <StatCard title="招聘类型" items={stats.recruitment} labels={RECRUITMENT_LABELS} accent />
        <StatCard title="城市分布" items={stats.city} />
        <StatCard title="薪资分布（月薪 K / 日薪）" items={stats.salary} />
        <StatCard title="学历要求" items={stats.education} />
        <StatCard title="经验要求" items={stats.experience} />
        <StatCard title="来源平台" items={stats.platform} labels={PLATFORM_LABELS} />
        <StatCard title="岗位最多的公司" items={stats.top_companies} />
        <StatCard title="JD 技能关键词" items={stats.skill_freq} accent />
        <StatCard title="JD 福利关键词" items={stats.welfare_freq} />
      </div>
    </div>
  )
}
