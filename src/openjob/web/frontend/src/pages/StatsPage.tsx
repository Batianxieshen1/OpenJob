import { useEffect, useMemo, useState } from 'react'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts'
import { Building2, GraduationCap, MapPin, Wallet } from 'lucide-react'
import { cn } from '@/lib/utils'

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

const DONUT_COLORS = [
  'rgb(49 87 232)',
  'rgb(49 87 232 / 0.65)',
  'rgb(49 87 232 / 0.42)',
  'rgb(49 87 232 / 0.25)',
  'rgb(49 87 232 / 0.15)',
  'rgb(49 87 232 / 0.08)',
]

function displayName(key: string, labels?: Record<string, string>): string {
  return labels?.[key] ?? key
}

function share(count: number, total: number) {
  return total > 0 ? Math.round((count / total) * 100) : 0
}

function BarList({ items, labels, accent = false }: { items: NameCount[]; labels?: Record<string, string>; accent?: boolean }) {
  const peak = Math.max(...items.map(i => i.count), 1)
  return (
    <ul className="space-y-2.5">
      {items.map(item => (
        <li key={item.name}>
          <div className="flex items-baseline justify-between gap-2 text-[13px]">
            <span className="truncate text-foreground">{displayName(item.name, labels)}</span>
            <span className="shrink-0 text-xs tabular-nums text-muted">
              {item.count} <span className="text-muted-3">· {share(item.count, peak)}%</span>
            </span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-surface-hover">
            <div
              className={cn('h-full rounded-full', accent ? 'bg-primary' : 'bg-primary/55')}
              style={{ width: `${Math.max(4, Math.round((item.count / peak) * 100))}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}

function DonutChart({ items, labels }: { items: NameCount[]; labels?: Record<string, string> }) {
  const top = items.slice(0, 5)
  const total = top.reduce((sum, i) => sum + i.count, 0) || 1
  return (
    <div className="flex items-center gap-4">
      <div className="relative h-[132px] w-[132px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={top}
              dataKey="count"
              nameKey="name"
              innerRadius={38}
              outerRadius={62}
              paddingAngle={2}
              strokeWidth={0}
            >
              {top.map((_, i) => (
                <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value, name) => [`${value} 个`, displayName(String(name), labels)]}
              contentStyle={{ borderRadius: 12, border: '1px solid rgb(var(--border-c))', fontSize: 12 }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <ul className="min-w-0 flex-1 space-y-1.5 text-xs">
        {top.map((item, i) => (
          <li key={item.name} className="flex items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1.5 text-muted">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
              <span className="truncate">{displayName(item.name, labels)}</span>
            </span>
            <span className="shrink-0 font-semibold tabular-nums text-foreground">{share(item.count, total)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ChipCloud({ items, accent = false }: { items: NameCount[]; accent?: boolean }) {
  const peak = Math.max(...items.map(i => i.count), 1)
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(item => {
        const ratio = item.count / peak
        const size = ratio > 0.66 ? 'text-[13px] font-semibold' : ratio > 0.33 ? 'text-xs' : 'text-[11px]'
        return (
          <span
            key={item.name}
            className={cn(
              'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 transition-soft',
              ratio > 0.66 && accent
                ? 'border-primary/40 bg-accent-soft text-primary'
                : 'border-card-border bg-surface-hover text-muted'
            )}
            title={`${item.name}：出现 ${item.count} 次`}
          >
            {item.name}
            <span className="tabular-nums opacity-70">{item.count}</span>
            <span className={cn('sr-only', size)}>{size}</span>
          </span>
        )
      })}
    </div>
  )
}

function RankList({ items }: { items: NameCount[] }) {
  const peak = Math.max(...items.map(i => i.count), 1)
  return (
    <ol className="space-y-2">
      {items.map((item, i) => (
        <li key={item.name} className="flex items-center gap-3">
          <span
            className={cn(
              'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums',
              i === 0 ? 'bg-ink text-shell' : 'bg-surface-hover text-muted'
            )}
          >
            {i + 1}
          </span>
          <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{item.name}</span>
          <div className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-surface-hover sm:block">
            <div className="h-full rounded-full bg-primary/55" style={{ width: `${Math.round((item.count / peak) * 100)}%` }} />
          </div>
          <span className="w-8 shrink-0 text-right text-xs tabular-nums text-muted">{item.count}</span>
        </li>
      ))}
    </ol>
  )
}

function ModuleCard({ title, hint, children, className }: { title: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-module border border-card-border bg-card p-5 shadow-card', className)}>
      <div className="mb-4 flex items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold">{title}</h3>
        {hint && <span className="text-[11px] text-muted-3">{hint}</span>}
      </div>
      {children}
    </section>
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

  const conclusions = useMemo(() => {
    if (!stats) return []
    const topCity = stats.city?.[0]
    const topSalary = stats.salary?.[0]
    const topSkill = stats.skill_freq?.[0]
    const topEdu = stats.education?.[0]
    const cards = [
      { icon: Building2, label: '样本岗位', value: String(stats.total), hint: SCOPE_LABELS[scope] },
      topCity && { icon: MapPin, label: '最集中城市', value: displayName(topCity.name), hint: `占 ${share(topCity.count, stats.total)}%` },
      topSalary && { icon: Wallet, label: '主流薪资段', value: topSalary.name, hint: `${topSalary.count} 个岗位` },
      topEdu && { icon: GraduationCap, label: '主流学历', value: topEdu.name, hint: `占 ${share(topEdu.count, stats.total)}%` },
    ]
    return cards.filter(Boolean) as Array<{ icon: typeof Building2; label: string; value: string; hint: string }>
  }, [stats, scope])

  const summary = useMemo(() => {
    if (!stats) return ''
    const parts: string[] = []
    const topCity = stats.city?.[0]
    const topSkill = stats.skill_freq?.[0]
    const campus = stats.recruitment?.find(r => r.name === 'campus')
    if (topCity) parts.push(`「${displayName(topCity.name)}」岗位最集中（占 ${share(topCity.count, stats.total)}%）`)
    if (topSkill) parts.push(`JD 里「${topSkill.name}」出现最多（${topSkill.count} 次），可以在简历里重点呼应`)
    if (campus) parts.push(`实习/校招类占 ${share(campus.count, stats.total)}%`)
    return parts.join('；') + '。'
  }, [stats, scope])

  if (loading) {
    return (
      <div className="rise-in mx-auto max-w-[1440px] space-y-4">
        <div className="skeleton h-8 w-48" />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="skeleton h-24 w-full" />
          ))}
          <div className="skeleton h-56 w-full md:col-span-2 xl:col-span-3" />
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
        <div className="rounded-module border border-dashed border-card-border px-6 py-16 text-center">
          <p className="text-sm text-foreground">还没有岗位数据</p>
          <p className="mt-1 text-xs text-muted">先在工作台运行一次采集并评分，之后这里会生成市场画像。</p>
          <button
            type="button"
            onClick={() => { window.location.href = '/' }}
            className="mt-4 rounded-full bg-ink px-5 py-2 text-xs font-semibold text-shell transition-soft hover:-translate-y-px"
          >
            去工作台运行采集
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="rise-in mx-auto max-w-[1440px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">市场分析</h1>
          <p className="mt-0.5 text-xs text-muted">
            基于{SCOPE_LABELS[scope]}（{stats.total} 个）的市场画像，随每轮采集自动更新。
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-full border border-card-border bg-card p-1" role="group" aria-label="统计范围">
          {(Object.keys(SCOPE_LABELS) as Scope[]).map(key => (
            <button
              key={key}
              type="button"
              onClick={() => setScope(key)}
              className={cn(
                'rounded-full px-3.5 py-1.5 text-xs font-semibold transition-soft',
                scope === key ? 'bg-ink text-shell' : 'text-muted hover:text-foreground'
              )}
            >
              {SCOPE_LABELS[key]}
            </button>
          ))}
        </div>
      </header>

      {/* 结论卡 */}
      <div className="stagger grid grid-cols-2 gap-3 xl:grid-cols-4">
        {conclusions.map(card => (
          <div key={card.label} className="rounded-card border border-card-border bg-card p-4 shadow-card">
            <div className="flex items-center gap-1.5 text-[11px] text-muted">
              <card.icon className="h-3.5 w-3.5" />
              {card.label}
            </div>
            <div className="mt-1.5 truncate text-xl font-semibold text-foreground">{card.value}</div>
            <div className="mt-0.5 text-[11px] text-muted-3">{card.hint}</div>
          </div>
        ))}
      </div>

      {/* 市场结论 */}
      {summary && (
        <div className="rounded-card border border-primary/20 bg-accent-soft px-5 py-3.5 text-[13px] leading-6 text-primary">
          <span className="mr-1.5 font-semibold">市场结论</span>
          {summary}
        </div>
      )}

      {/* 分布模块 */}
      <div className="stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12 [&>*]:min-w-0">
        {stats.city && stats.city.length > 0 && (
          <div className="xl:col-span-6">
            <ModuleCard title="城市分布" hint="岗位数与占比">
              <BarList items={stats.city.slice(0, 8)} />
            </ModuleCard>
          </div>
        )}
        {stats.education && stats.education.length > 0 && (
          <div className="xl:col-span-3">
            <ModuleCard title="学历要求">
              <DonutChart items={stats.education} />
            </ModuleCard>
          </div>
        )}
        {stats.recruitment && stats.recruitment.length > 0 && (
          <div className="xl:col-span-3">
            <ModuleCard title="招聘类型">
              <DonutChart items={stats.recruitment} labels={RECRUITMENT_LABELS} />
            </ModuleCard>
          </div>
        )}
        {stats.salary && stats.salary.length > 0 && (
          <div className="xl:col-span-6">
            <ModuleCard title="薪资分布" hint="月薪 K / 日薪">
              <BarList items={stats.salary.slice(0, 8)} accent />
            </ModuleCard>
          </div>
        )}
        {stats.top_companies && stats.top_companies.length > 0 && (
          <div className="xl:col-span-3">
            <ModuleCard title="岗位最多的公司">
              <RankList items={stats.top_companies.slice(0, 6)} />
            </ModuleCard>
          </div>
        )}
        {stats.platform && stats.platform.length > 0 && (
          <div className="xl:col-span-3">
            <ModuleCard title="来源平台">
              <div className="flex flex-wrap gap-1.5">
                {stats.platform.map(p => (
                  <span key={p.name} className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-3 py-1.5 text-xs font-semibold text-primary">
                    {displayName(p.name, PLATFORM_LABELS)}
                    <span className="tabular-nums opacity-70">{p.count}</span>
                  </span>
                ))}
              </div>
              {stats.experience && stats.experience.length > 0 && (
                <div className="mt-4 border-t border-card-border pt-3">
                  <div className="mb-2 text-xs text-muted">经验要求</div>
                  <BarList items={stats.experience.slice(0, 4)} />
                </div>
              )}
            </ModuleCard>
          </div>
        )}
        {stats.skill_freq && stats.skill_freq.length > 0 && (
          <div className="xl:col-span-6">
            <ModuleCard title="JD 技能关键词" hint="字号越大出现越多">
              <ChipCloud items={stats.skill_freq.slice(0, 18)} accent />
            </ModuleCard>
          </div>
        )}
        {stats.welfare_freq && stats.welfare_freq.length > 0 && (
          <div className="xl:col-span-6">
            <ModuleCard title="JD 福利关键词" hint="警惕与 JD 原文不符的标签">
              <ChipCloud items={stats.welfare_freq.slice(0, 18)} />
            </ModuleCard>
          </div>
        )}
      </div>
    </div>
  )
}
