import { useEffect, useMemo, useState } from 'react'
import { useDashboard, type Job } from '@/hooks/useDashboard'
import { useDebouncedValue, EMPTY_JOB_FILTERS, hasActiveJobFilters, hasInvalidSalaryRange, filterJobs, type JobFilters } from '@/lib/jobFilters'
import { Button } from '@/components/ui/button'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { JobActionCard, JobDetailModal } from '@/pages/DashboardPage'

interface WorkbenchData {
  pending_confirmation: Job[]
  send_errors: Job[]
}

export default function ConfirmQueuePage() {
  const { workbench, loading, refresh } = useDashboard('workbench')
  const [selected, setSelected] = useState<string[]>([])
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [notice, setNotice] = useState('')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const debouncedQuery = useDebouncedValue(filters.query, 250)

  const pendingJobs = workbench.pending_confirmation || []
  const jobs = useMemo(
    () => pendingJobs.filter(job => !workbench.send_errors?.some(err => err.id === job.id)),
    [pendingJobs, workbench.send_errors],
  )
  const effectiveFilters = useMemo<JobFilters>(() => ({ ...filters, query: debouncedQuery }), [filters, debouncedQuery])
  const filtered = useMemo(() => filterJobs(jobs, effectiveFilters), [jobs, effectiveFilters])
  const actionable = useMemo(() => selected.filter(id => filtered.some(job => job.id === id)), [selected, filtered])

  useEffect(() => {
    setSelected(prev => prev.filter(id => jobs.some(job => job.id === id)))
  }, [jobs])

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
    <div className="rise-in mx-auto max-w-[1440px] space-y-4">
      <header>
        <h1 className="text-lg font-bold">投递确认</h1>
        <p className="text-xs text-muted">
          这里是投递前的人工闸门：AI 只建议，你拍板。勾选岗位 → 一键投递 → 招呼语生成后按安全队列发送。
        </p>
      </header>

      {notice && <div className="rounded-card border border-card-border bg-card px-4 py-3 text-sm text-foreground">{notice}</div>}

      <section className="rounded-3xl border border-card-border bg-card p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div className="text-xs text-muted">
            待确认 <span className="font-bold text-foreground">{jobs.length}</span> 个
            {workbench.send_errors && workbench.send_errors.length > 0 && (
              <span className="ml-2 text-warning">另有 {workbench.send_errors.length} 个发送失败岗位在岗位池待处理</span>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => setSelected(filtered.map(job => job.id))}>全选</Button>
            <Button variant="secondary" size="sm" onClick={() => setSelected([])}>清空</Button>
            <Button variant="secondary" size="sm" onClick={() => rejectSelected(actionable)}>放弃已选 {actionable.length} 个</Button>
            <Button size="sm" onClick={() => confirmDeliver(actionable)}>一键投递已选 {actionable.length} 个</Button>
          </div>
        </div>
        <JobFilterBar
          filters={filters}
          onChange={setFilters}
          onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
          resultCount={filtered.length}
          totalCount={jobs.length}
          invalidSalary={hasInvalidSalaryRange(filters)}
        />
        {filtered.length ? (
          <div className="stagger grid grid-cols-1 gap-3 lg:grid-cols-2">
            {filtered.map(job => (
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
        ) : jobs.length ? (
          <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover p-5 text-center text-sm text-muted">
            <p>没有符合当前条件的岗位</p>
            <Button className="mt-3" variant="secondary" size="sm" onClick={() => setFilters({ ...EMPTY_JOB_FILTERS })}>重置筛选</Button>
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
