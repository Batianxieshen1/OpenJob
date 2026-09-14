import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDashboard, type Job } from '@/hooks/useDashboard'
import { useDebouncedValue, EMPTY_JOB_FILTERS, hasActiveJobFilters, hasInvalidSalaryRange, filterJobs, type JobFilters } from '@/lib/jobFilters'
import { Button } from '@/components/ui/button'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { JobActionCard, JobDetailModal, waitingDays } from '@/components/jobs/JobCards'
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

/** B9 六要素批量确认对话框：发送数/平台分布/事实校验/需检查/额度 + 不可撤回警示 */
function BatchConfirmDialog({
  jobs, quotaRemaining, submitting, onConfirm, onClose,
}: {
  jobs: Job[]
  quotaRemaining: number
  submitting: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  const platformCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const job of jobs) {
      const platform = job.source_platform || 'boss'
      counts[platform] = (counts[platform] || 0) + 1
    }
    return counts
  }, [jobs])
  const platformLabel: Record<string, string> = { boss: 'BOSS直聘', zhilian: '智联招聘', '51job': '前程无忧' }
  const verified = jobs.filter(job => (job.greeting || '').trim() && job.greeting_fact_status === 'verified').length
  const noGreeting = jobs.filter(job => !(job.greeting || '').trim()).length
  const needCheck = jobs.filter(job => (job.greeting || '').trim() && job.greeting_fact_status !== 'verified').length
  const total = jobs.length

  return createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm" onMouseDown={e => { if (e.target === e.currentTarget && !submitting) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="批量确认发送" className="w-full max-w-lg rounded-overlay border border-card-border bg-card p-6 shadow-pop">
        <h3 className="text-lg font-semibold">批量确认发送</h3>
        <ul className="mt-4 space-y-2 text-sm">
          <li>本次将发送 <span className="font-semibold text-primary tabular-nums">{total}</span> 条招呼语</li>
          <li>平台分布：{Object.entries(platformCounts).map(([p, n]) => `${platformLabel[p] || p} ${n}`).join('，') || '-'}</li>
          <li>已过事实校验（可发送）：<span className="font-semibold text-success tabular-nums">{verified}</span> 条；无招呼语将现场生成：<span className="tabular-nums">{noGreeting}</span> 条</li>
          <li>需人工检查（未过事实校验，会被跳过）：<span className={cn('tabular-nums', needCheck > 0 && 'font-semibold text-warning')}>{needCheck}</span> 条</li>
          <li>今日剩余额度：<span className="tabular-nums">{quotaRemaining}</span> 条{total > quotaRemaining && <span className="ml-1 text-warning">（超出部分明日时间窗自动续发）</span>}</li>
        </ul>
        <p className="mt-4 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          ⚠ 发送后不可自动撤回：招呼语会按安全队列（时间窗/间隔/每日上限）逐条发出，请确认所选岗位均为你真实想投的。
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" size="sm" disabled={submitting} onClick={onClose}>再看看</Button>
          <Button size="sm" disabled={submitting} onClick={onConfirm}>
            {submitting ? '提交中…' : `确认发送 ${total} 条招呼语`}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

export default function ConfirmQueuePage() {
  const { workbench, loading, refresh } = useDashboard('workbench')
  const [selected, setSelected] = useState<string[]>([])
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [notice, setNotice] = useState('')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [quickFilter, setQuickFilter] = useState<QuickFilter>('all')
  const [page, setPage] = useState(0)
  const [batchTarget, setBatchTarget] = useState<string[] | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
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
    setBatchTarget(ids)
  }

  const runBatchDeliver = async () => {
    const ids = batchTarget || []
    if (!ids.length || batchSubmitting) return
    setBatchSubmitting(true)
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
      setNotice(`已确认投递 ${ids.length} 个岗位，后端按队列推进（受时间窗与每日额度限制）。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '投递失败')
    } finally {
      setBatchSubmitting(false)
      setBatchTarget(null)
    }
  }

  const staleExitOverdue = async () => {
    const overdueIds = jobs.filter(job => selected.includes(job.id) && waitingDays(job) > 7)
    if (!overdueIds.length) {
      setNotice('已选岗位中没有等待超过 7 天的岗位。')
      return
    }
    if (!window.confirm(`过期退出会把手选的 ${overdueIds.length} 个超 7 天岗位移出确认队列（状态变为「超期退出」，可随时重新激活）。继续吗？`)) return
    try {
      const res = await fetch('/api/jobs/stale-exit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: overdueIds.map(job => job.id) }),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(payload.error || '过期退出失败')
      setSelected([])
      await refresh()
      const skipped = Array.isArray(payload?.data?.skipped) ? payload.data.skipped.length : 0
      setNotice(`已过期退出 ${payload?.data?.stale_count ?? overdueIds.length} 个岗位${skipped ? `，${skipped} 个因状态不允许被跳过` : ''}。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '过期退出失败')
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
            <Button variant="secondary" size="sm" onClick={staleExitOverdue}>超 7 天过期退出</Button>
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
      {batchTarget && batchTarget.length > 0 && (
        <BatchConfirmDialog
          jobs={jobs.filter(job => batchTarget.includes(job.id))}
          quotaRemaining={workbench.send_quota?.remaining ?? 0}
          submitting={batchSubmitting}
          onConfirm={runBatchDeliver}
          onClose={() => { if (!batchSubmitting) setBatchTarget(null) }}
        />
      )}
    </div>
  )
}
