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
  jobs, quotaRemaining, submitting, generateOnly, onConfirm, onClose,
}: {
  jobs: Job[]
  quotaRemaining: number
  submitting: boolean
  generateOnly?: boolean
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
      <div role="dialog" aria-modal="true" aria-label="批量确认" className="w-full max-w-lg rounded-overlay border border-card-border bg-card p-6 shadow-pop">
        <h3 className="t-h2">{generateOnly ? '批量生成招呼语' : '批量确认发送'}</h3>
        <ul className="mt-4 space-y-2 text-sm">
          <li>本次将为 <span className="font-semibold text-primary tabular-nums">{total}</span> 个岗位生成招呼语（<span className="font-semibold">只生成，不发送</span>）</li>
          <li>平台分布：{Object.entries(platformCounts).map(([p, n]) => `${platformLabel[p] || p} ${n}`).join('，') || '-'}</li>
          <li>生成后全部停在「待发送招呼语」等你逐条审阅，你确认后才会进入发送队列</li>
          <li>已过事实校验：<span className="font-semibold text-success tabular-nums">{verified}</span> 条；无招呼语将现场生成：<span className="tabular-nums">{noGreeting}</span> 条；需人工检查：<span className={cn('tabular-nums', needCheck > 0 && 'font-semibold text-warning')}>{needCheck}</span> 条</li>
          <li>今日剩余发送额度：<span className="tabular-nums">{quotaRemaining}</span> 条（发送阶段另受时间窗与每日上限约束）</li>
        </ul>
        <p className="mt-4 rounded-xl border border-primary/30 bg-accent-soft px-3 py-2 text-xs text-primary">
          两段式流程：本步只生成不发送 → 到「待发送招呼语」逐条审阅 → 满意后点「一键投递（不重新生成）」进入发送队列。
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" size="sm" disabled={submitting} onClick={onClose}>再看看</Button>
          <Button size="sm" disabled={submitting} onClick={onConfirm}>
            {submitting ? '提交中…' : `确认生成 ${total} 条招呼语`}
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
  const [batchTarget, setBatchTarget] = useState<{ ids: string[]; directSend: boolean } | null>(null)
  const [batchSubmitting, setBatchSubmitting] = useState(false)
  const [tab, setTab] = useState<'confirm' | 'ready_to_send'>('confirm')
  const [sendSelected, setSendSelected] = useState<string[]>([])
  const [editingGreeting, setEditingGreeting] = useState<{ jobId: string; text: string } | null>(null)
  const [editSaving, setEditSaving] = useState(false)
  const debouncedQuery = useDebouncedValue(filters.query, 250)

  const readyToSendJobs = workbench.pending_greetings || []

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

  const confirmDeliver = async (ids: string[], directSend = false) => {
    if (!ids.length) return
    setBatchTarget({ ids, directSend })
  }

  const runBatchDeliver = async () => {
    const ids = batchTarget?.ids || []
    const directSend = batchTarget?.directSend || false
    if (!ids.length || batchSubmitting) return
    setBatchSubmitting(true)
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          directSend
            ? { job_ids: ids, direct_send: true }
            : { job_ids: ids, generate_only: true },
        ),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '投递失败')
      }
      setSelected(prev => prev.filter(id => !ids.includes(id)))
      setSendSelected([])
      await refresh()
      setNotice(
        directSend
          ? `已把 ${ids.length} 个岗位加入发送队列（招呼语不重新生成，按安全队列直接发送）。`
          : `已生成 ${ids.length} 个岗位的招呼语，全部停在「待发送招呼语」等你审阅；确认后才会发送（本步未发送任何内容）。`
      )
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

  const saveGreetingEdit = async () => {
    if (!editingGreeting || editSaving) return
    const text = editingGreeting.text.trim()
    if (!text) {
      setNotice('招呼语不能为空。')
      return
    }
    setEditSaving(true)
    try {
      const res = await fetch(`/api/jobs/${editingGreeting.jobId}/greeting`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ greeting: text }),
      })
      const payload = await res.json()
      if (!res.ok) {
        const issues = payload?.error?.details?.issues as string[] | undefined
        throw new Error(issues?.length ? `${payload.error.message}：${issues[0]}` : payload?.error?.message || '保存失败')
      }
      setEditingGreeting(null)
      await refresh()
      setNotice('招呼语已保存并通过事实校验。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '保存失败')
    } finally {
      setEditSaving(false)
    }
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载中...</div>
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="t-h1">投递确认</h1>
          <p className="text-xs text-muted">
            这里是投递前的人工闸门：AI 只建议，你拍板。勾选岗位 → 一键投递 → 招呼语生成后按安全队列发送。
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-full border border-card-border bg-card p-1">
          <button
            type="button"
            onClick={() => setTab('confirm')}
            className={cn(
              'rounded-full px-3 py-1.5 text-xs font-semibold transition-soft',
              tab === 'confirm' ? 'bg-ink text-shell' : 'text-muted hover:text-foreground'
            )}
          >
            待确认生成（{jobs.length}）
          </button>
          <button
            type="button"
            onClick={() => setTab('ready_to_send')}
            className={cn(
              'rounded-full px-3 py-1.5 text-xs font-semibold transition-soft',
              tab === 'ready_to_send' ? 'bg-ink text-shell' : 'text-muted hover:text-foreground'
            )}
          >
            待发送招呼语（{readyToSendJobs.length}）
          </button>
        </div>
      </header>

      {notice && <div className="rise-in rounded-card border border-card-border bg-card px-4 py-3 text-sm text-foreground">{notice}</div>}

      {workbench.today_day_off && (
        <div className="rise-in rounded-card border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
          🎲 今日为防检测随机休息日：发送已冻结，岗位全部保留在「待发送招呼语」（每天 5% 概率随机抽取，模拟真人节奏）；明日 09:00 后自动恢复发送。
        </div>
      )}

      {tab === 'ready_to_send' ? (
        <section className="rounded-module border border-card-border bg-card p-5">
          <div className="sticky top-0 z-20 -mx-5 mb-3 rounded-t-module border-b border-card-border bg-shell/95 px-5 py-3 backdrop-blur">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-muted">
                招呼语已生成并通过事实校验的岗位 <span className="font-semibold text-foreground tabular-nums">{readyToSendJobs.length}</span> 个，等待进入发送队列
                {sendSelected.length > 0 && <span className="ml-1 text-primary">· 已选 {sendSelected.length}</span>}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={!readyToSendJobs.length} onClick={() => setSendSelected(readyToSendJobs.map(job => job.id))}>全选</Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!readyToSendJobs.length || !(workbench.send_quota?.remaining ?? 0)}
                  onClick={() => setSendSelected(readyToSendJobs.slice(0, workbench.send_quota?.remaining ?? 0).map(job => job.id))}
                >
                  只选额度内（{Math.min(readyToSendJobs.length, workbench.send_quota?.remaining ?? 0)}）
                </Button>
                <Button variant="secondary" size="sm" disabled={!sendSelected.length} onClick={() => setSendSelected([])}>清空选择</Button>
                <Button size="sm" disabled={!sendSelected.length} onClick={() => confirmDeliver(sendSelected, true)}>一键投递已选 {sendSelected.length}（不重新生成）</Button>
              </div>
            </div>
          </div>
          {readyToSendJobs.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover p-6 text-center text-sm text-muted">
              暂无待发送岗位。在「待确认生成」里确认的岗位生成招呼语后会出现在这里。
            </div>
          ) : (
            <ul className="space-y-3">
              {readyToSendJobs.map(job => (
                <li key={job.id} className={cn(
                  'rounded-card border p-4 transition-soft',
                  sendSelected.includes(job.id) ? 'border-primary bg-accent-soft/40' : 'border-card-border bg-surface-hover'
                )}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          type="checkbox"
                          checked={sendSelected.includes(job.id)}
                          onChange={() => setSendSelected(prev => (prev.includes(job.id) ? prev.filter(id => id !== job.id) : [...prev, job.id]))}
                          aria-label={`选择岗位：${job.company} ${job.title}`}
                          className="h-4 w-4 accent-primary"
                        />
                        <span className="font-semibold text-foreground">{job.company}｜{job.title}</span>
                        <span className="text-xs text-muted tabular-nums">{job.score} 分</span>
                        {waitingDays(job) >= 2 && (
                          <span className={cn('text-xs', waitingDays(job) > 7 ? 'font-semibold text-warning' : 'text-muted')}>
                            已等待 {waitingDays(job)} 天
                          </span>
                        )}
                      </div>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground">{job.greeting}</p>
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => setEditingGreeting({ jobId: job.id, text: job.greeting || '' })}>
                      编辑招呼语
                    </Button>
                  </div>
                  {editingGreeting?.jobId === job.id && (
                    <div className="mt-3 rounded-xl border border-card-border bg-card p-3">
                      <textarea
                        value={editingGreeting.text}
                        onChange={event => setEditingGreeting({ jobId: job.id, text: event.target.value })}
                        rows={4}
                        className="w-full rounded-lg border border-card-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary"
                        aria-label={`编辑招呼语：${job.company}`}
                      />
                      <div className="mt-2 flex items-center justify-end gap-2">
                        <Button variant="secondary" size="sm" disabled={editSaving} onClick={() => setEditingGreeting(null)}>取消</Button>
                        <Button size="sm" disabled={editSaving} onClick={() => void saveGreetingEdit()}>
                          {editSaving ? '校验中…' : '保存（重新过事实校验）'}
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
      <>
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
      </>
      )}

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
      {batchTarget && batchTarget.ids.length > 0 && (
        <BatchConfirmDialog
          jobs={(batchTarget.directSend ? readyToSendJobs : jobs).filter(job => batchTarget.ids.includes(job.id))}
          quotaRemaining={workbench.send_quota?.remaining ?? 0}
          submitting={batchSubmitting}
          generateOnly={!batchTarget.directSend}
          onConfirm={runBatchDeliver}
          onClose={() => { if (!batchSubmitting) setBatchTarget(null) }}
        />
      )}
    </div>
  )
}
