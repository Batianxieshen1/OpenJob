import { useEffect, useMemo, useState } from 'react'
import { useDashboard, type Job } from '@/hooks/useDashboard'
import { useDebouncedValue, EMPTY_JOB_FILTERS, hasActiveJobFilters, hasInvalidSalaryRange, filterJobs, type JobFilters } from '@/lib/jobFilters'
import { Button } from '@/components/ui/button'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { JobActionCard, JobDetailModal } from '@/components/jobs/JobCards'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 16

type QuickFilter = 'all' | 'high_match' | 'active_today'

const QUICK_FILTERS: Array<{ key: QuickFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'high_match', label: '高匹配 ≥80' },
  { key: 'active_today', label: '今日活跃' },
]

function isActiveToday(job: Job) {
  return Boolean(job.hr_active && (job.hr_active.includes('分钟') || job.hr_active.includes('在线') || job.hr_active.includes('今日')))
}

export default function ConfirmQueuePage() {
  const { workbench, loading, refresh } = useDashboard('workbench')
  const [selected, setSelected] = useState<string[]>([])
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [notice, setNotice] = useState('')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [quickFilter, setQuickFilter] = useState<QuickFilter>('all')
  const [page, setPage] = useState(0)
  const debouncedQuery = useDebouncedValue(filters.query, 250)

  const pendingJobs = workbench.pending_confirmation || []
  const jobs = useMemo(
    () => pendingJobs.filter(job => !workbench.send_errors?.some(err => err.id === job.id)),
    [pendingJobs, workbench.send_errors],
  )
  const effectiveFilters = useMemo<JobFilters>(() => ({ ...filters, query: debouncedQuery }), [filters, debouncedQuery])
  const filtered = useMemo(() => filterJobs(jobs, effectiveFilters), [jobs, effectiveFilters])
  const quickFiltered = useMemo(() => {
    if (quickFilter === 'high_match') return filtered.filter(job => (job.score || 0) >= 80)
    if (quickFilter === 'active_today') return filtered.filter(isActiveToday)
    return filtered
  }, [filtered, quickFilter])
  // 高分优先展示：评分降序，方便先处理最值得投的
  const sorted = useMemo(
    () => [...quickFiltered].sort((a, b) => (b.score || 0) - (a.score || 0)),
    [quickFiltered],
  )
  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages - 1)
  const pageJobs = useMemo(() => sorted.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE), [sorted, safePage])
  const actionable = useMemo(() => selected.filter(id => filtered.some(job => job.id === id)), [selected, filtered])

  useEffect(() => {
    setSelected(prev => prev.filter(id => jobs.some(job => job.id === id)))
  }, [jobs])

  useEffect(() => {
    setPage(0)
  }, [quickFilter, filters])

  const confirmDeliver = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    if (!window.confirm(`是否投递以下 ${count} 个岗位？确认后将生成定制招呼语并按安全队列发送。`)) return
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '投递失败')
      }
      setSelected(prev => prev.filter(id => !ids.includes(id)))
      await refresh()
      setNotice(`已确认投递 ${count} 个岗位，后端按队列推进（受时间窗与每日额度限制）。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '投递失败')
    }
  }

  const rejectSelected = async (ids: string[]) => {
    if (!ids.length) return
    if (!window.confirm(`确定放弃这 ${ids.length} 个岗位吗？`)) return
    const companies = [...new Set(jobs.filter(job => ids.includes(job.id)).map(job => job.company).filter(Boolean))]
    let blockCompanies: string[] = []
    if (companies.length) {
      const label = companies.length <= 3 ? companies.join('、') : `${companies.slice(0, 3).join('、')} 等 ${companies.length} 家`
      if (window.confirm(`是否将 ${label} 加入公司黑名单？此后新岗位会被自动预筛过滤。`)) {
        blockCompanies = companies
      }
    }
    try {
      const res = await fetch('/api/workbench/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids, block_companies: blockCompanies }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放弃失败')
      }
      setSelected(prev => prev.filter(id => !ids.includes(id)))
      await refresh()
      setNotice(blockCompanies.length ? `已放弃 ${ids.length} 个岗位，${blockCompanies.length} 家公司已加入黑名单。` : `已放弃 ${ids.length} 个岗位。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放弃失败')
    }
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载中...</div>
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-4">
      <header>
        <h1 className="text-lg font-semibold">投递确认</h1>
        <p className="text-xs text-muted">
          这里是投递前的人工闸门：AI 只建议，你拍板。勾选岗位 → 一键投递 → 招呼语生成后按安全队列发送。
        </p>
      </header>

      {notice && <div className="rise-in rounded-card border border-card-border bg-card px-4 py-3 text-sm text-foreground">{notice}</div>}

      {/* 批量操作栏：吸顶，滚动时始终可操作 */}
      <div className="sticky top-0 z-30 rounded-card border border-card-border bg-shell/95 px-4 py-3 shadow-card backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">
              待确认 <span className="font-semibold text-foreground tabular-nums">{jobs.length}</span> 个
              {selected.length > 0 && <span className="ml-1 text-primary">· 已选 {selected.length}</span>}
            </span>
            <div className="flex items-center gap-1 rounded-full border border-card-border bg-card p-1">
              {QUICK_FILTERS.map(item => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setQuickFilter(item.key)}
                  className={cn(
                    'rounded-full px-3 py-1 text-xs font-semibold transition-soft',
                    quickFilter === item.key ? 'bg-ink text-shell' : 'text-muted hover:text-foreground'
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {workbench.send_errors && workbench.send_errors.length > 0 && (
              <span className="hidden text-xs text-warning md:inline">另有 {workbench.send_errors.length} 个发送失败岗位在岗位池待处理</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => setSelected(filtered.map(job => job.id))}>全选本页结果</Button>
            <Button variant="secondary" size="sm" onClick={() => setSelected([])}>清空</Button>
            <Button variant="secondary" size="sm" onClick={() => rejectSelected(actionable)}>放弃已选 {actionable.length}</Button>
            <Button size="sm" onClick={() => confirmDeliver(actionable)}>一键投递已选 {actionable.length}</Button>
          </div>
        </div>
      </div>

      <section className="rounded-module border border-card-border bg-card p-5">
        <JobFilterBar
          filters={filters}
          onChange={setFilters}
          onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
          resultCount={sorted.length}
          totalCount={jobs.length}
          invalidSalary={hasInvalidSalaryRange(filters)}
        />
        {pageJobs.length ? (
          <>
            <div className="stagger grid grid-cols-1 gap-3 lg:grid-cols-2">
              {pageJobs.map(job => (
                <JobActionCard
                  key={job.id}
                  job={job}
                  selected={selected.includes(job.id)}
                  onToggle={() => setSelected(prev => (prev.includes(job.id) ? prev.filter(id => id !== job.id) : [...prev, job.id]))}
                  onDetail={() => setSelectedJob(job)}
                  onReject={() => rejectSelected([job.id])}
                />
              ))}
            </div>
            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-center gap-2 text-xs text-muted">
                <Button variant="secondary" size="sm" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</Button>
                <span className="tabular-nums">第 {safePage + 1} / {totalPages} 页 · 共 {sorted.length} 个</span>
                <Button variant="secondary" size="sm" disabled={safePage >= totalPages - 1} onClick={() => setPage(safePage + 1)}>下一页</Button>
              </div>
            )}
          </>
        ) : jobs.length ? (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover p-5 text-center text-sm text-muted">
            <p>没有符合当前条件的岗位</p>
            <Button className="mt-3" variant="secondary" size="sm" onClick={() => { setFilters({ ...EMPTY_JOB_FILTERS }); setQuickFilter('all') }}>重置筛选</Button>
          </div>
        ) : hasActiveJobFilters(filters) ? (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover p-5 text-center text-sm text-muted">没有符合筛选条件的岗位。</div>
        ) : (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover p-5 text-sm text-muted">
            暂无待确认岗位。新一轮采集评分完成后，过线岗位会出现在这里等你拍板。
          </div>
        )}
      </section>

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
    </div>
  )
}
