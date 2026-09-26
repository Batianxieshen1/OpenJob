import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDashboard, type Job } from '@/hooks/useDashboard'
import { useJobSearch, type JobSortKey, type JobSortOrder } from '@/hooks/useJobSearch'
import { Button } from '@/components/ui/button'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { RecycleBinPanel } from '@/components/dashboard/RecycleBinPanel'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { JobDetailModal } from '@/components/jobs/JobCards'
import { EmptyState } from '@/components/ui/EmptyState'
import { hasInvalidSalaryRange, EMPTY_JOB_FILTERS, filterJobs, type JobFilters } from '@/lib/jobFilters'
import { getStatusLabel } from '@/lib/status'
import { AlertTriangle, BriefcaseBusiness, Download, ExternalLink, Send, Eye, Trash2 } from 'lucide-react'

function ExportMenu({
  onExport,
  hasSelection,
  hasFiltered,
}: {
  onExport: (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => void
  hasSelection: boolean
  hasFiltered: boolean
}) {
  const [format, setFormat] = useState<'xlsx' | 'csv'>('xlsx')
  return (
    <div className="ml-auto flex flex-wrap items-center gap-2">
      <select
        value={format}
        onChange={event => setFormat(event.target.value as 'xlsx' | 'csv')}
        className="rounded-xl border border-card-border bg-card px-2 py-2 text-xs outline-none focus:border-primary"
      >
        <option value="xlsx">XLSX</option>
        <option value="csv">CSV</option>
      </select>
      <Button variant="secondary" size="sm" disabled={!hasFiltered} onClick={() => onExport(format, 'filtered')}>导出筛选结果</Button>
      <Button variant="secondary" size="sm" disabled={!hasSelection} onClick={() => onExport(format, 'selected')}>导出所选岗位</Button>
      <Button variant="secondary" size="sm" onClick={() => onExport(format, 'all')}>导出全部岗位</Button>
    </div>
  )
}


export default function JobsPoolPage() {
  const pageSize = 15
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [batchApproving, setBatchApproving] = useState(false)
  const [approvePreview, setApprovePreview] = useState<{
    filteredCount: number
    skippedCount: number
    scoreBuckets: Array<{ label: string; count: number }>
    ids: string[]
  } | null>(null)
  const [detailJob, setDetailJob] = useState<Job | null>(null)
  const [notice, setNotice] = useState('')
  const [showRecycleBin, setShowRecycleBin] = useState(false)
  const [showScoreDialog, setShowScoreDialog] = useState(false)
  const [quickScoring, setQuickScoring] = useState(false)
  const [sortBy, setSortBy] = useState<JobSortKey>('created_at')
  const [sortOrder, setSortOrder] = useState<JobSortOrder>('desc')
  const [recycleJobs, setRecycleJobs] = useState<Job[]>([])
  const [recycleSelectedIds, setRecycleSelectedIds] = useState<string[]>([])
  const [recycleLoading, setRecycleLoading] = useState(false)
  const [permanentDeleteIds, setPermanentDeleteIds] = useState<string[]>([])
  const [permanentDeleteAcknowledged, setPermanentDeleteAcknowledged] = useState(false)
  const { items, total, allTotal, loading, error, refresh: refreshJobs } = useJobSearch(filters, page, pageSize, sortBy, sortOrder)
  const { workbench: deliveryWorkbench } = useDashboard('workbench')
  const deliveryTask = deliveryWorkbench.task?.mode === 'deliver'
    ? deliveryWorkbench.task
    : deliveryWorkbench.last_task?.mode === 'deliver' ? deliveryWorkbench.last_task : null

  useEffect(() => {
    setPage(0)
  }, [filters.query, filters.minScore, filters.salaryMin, filters.salaryMax, filters.status, filters.createdWithin, filters.sourcePlatform, filters.education, filters.recruitmentType])

  const toggleSelected = (jobId: string) => {
    setSelectedIds(previous => previous.includes(jobId) ? previous.filter(id => id !== jobId) : [...previous, jobId])
  }

  const allPageSelected = items.length > 0 && items.every(job => selectedIds.includes(job.id))
  const toggleCurrentPage = () => {
    const pageIds = new Set(items.map(job => job.id))
    setSelectedIds(previous => allPageSelected
      ? previous.filter(id => !pageIds.has(id))
      : [...new Set([...previous, ...pageIds])])
  }

  const changeSort = (nextSortBy: JobSortKey) => {
    setPage(0)
    if (nextSortBy === sortBy) {
      setSortOrder(previous => previous === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortBy(nextSortBy)
    setSortOrder(nextSortBy === 'score' || nextSortBy === 'created_at' ? 'desc' : 'asc')
  }

  const loadRecycleBin = async () => {
    setRecycleLoading(true)
    try {
      const collected: Job[] = []
      let offset = 0
      const limit = 200
      while (true) {
        const res = await fetch(`/api/jobs?deleted=only&limit=${limit}&offset=${offset}`, { cache: 'no-store' })
        if (!res.ok) throw new Error(`回收站接口返回 ${res.status}`)
        const pageItems = await res.json()
        if (!Array.isArray(pageItems)) throw new Error('回收站响应格式无效')
        collected.push(...pageItems)
        const totalCount = Number(res.headers.get('X-Total-Count'))
        if (!pageItems.length || pageItems.length < limit || (Number.isFinite(totalCount) && collected.length >= totalCount)) break
        offset += pageItems.length
      }
      const unique = new Map(collected.map(job => [String(job.id), job]))
      setRecycleJobs([...unique.values()])
      setRecycleSelectedIds(previous => previous.filter(id => unique.has(id)))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '读取回收站失败')
    } finally {
      setRecycleLoading(false)
    }
  }

  useEffect(() => {
    void loadRecycleBin()
  }, [])

  const postJobAction = async (path: string, payload: Record<string, unknown>) => {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const blocked = Array.isArray(data.blocked)
        ? data.blocked.map((item: { job_id?: string; reasons?: string[] }) => `${item.job_id || '岗位'}：${(item.reasons || []).join('、')}`).join('；')
        : ''
      throw new Error([data.error || '岗位操作失败', blocked].filter(Boolean).join('；'))
    }
    return res.json()
  }

  const softDelete = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确认将 ${jobIds.length} 个岗位移入回收站吗？岗位不会永久删除。`)) return
    try {
      const result = await postJobAction('/api/jobs/soft-delete', { job_ids: jobIds, confirmed: true })
      setSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已移入回收站 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '移入回收站失败')
    }
  }

    const approveJob = async (job: Job) => {
    if (!window.confirm(`确定将「${job.company}｜${job.title}」放行到确认队列吗？AI 评分为 ${job.score ?? 0} 分（低于阈值），放行代表你的人工判断。`)) return
    try {
      const res = await fetch(`/api/jobs/${job.id}/approve`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放行失败')
      }
      await refreshJobs?.()
      setNotice(`已放行 ${job.company}｜${job.title}，去「投递确认」页即可投递。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放行失败')
    }
  }

const markManuallySent = async (job: Job) => {
    if (job.source_platform !== 'zhilian' && job.source_platform !== '51job') return
    const platformLabel = job.source_platform === 'zhilian' ? '智联招聘' : '前程无忧'
    if (!window.confirm(`请确认：你已经在${platformLabel}完成了这个岗位的投递。此操作只更新 OpenJob 本地记录，不会向平台发送任何内容。`)) return
    try {
      const result = await postJobAction('/api/jobs/manual-sent', {
        job_ids: [job.id],
        confirmed: true,
      })
      refreshJobs()
      setNotice(
        result.affected_count
          ? `已将 ${platformLabel} 岗位标记为“已发送”。`
          : `该岗位此前已经标记为“已发送”。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '标记已发送失败')
    }
  }

  const deliverSelectedJobs = async () => {
    if (!selectedIds.length) return
    const count = selectedIds.length
    if (!window.confirm(`确认投递已选择的 ${count} 个岗位吗？仅 BOSS 岗位可进入发送队列，且仍受发送时间窗口和每日额度限制。`)) return
    try {
      const result = await postJobAction('/api/workbench/deliver', { job_ids: selectedIds })
      setSelectedIds([])
      refreshJobs()
      setNotice(
        result.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中。`
          : result.queued_count
            ? `已将 ${result.queued_count} 个岗位追加到当前发送队列。`
            : `已确认投递 ${count} 个岗位，后端会按安全队列推进。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '一键投递失败')
    }
  }

  const bulkApproveSelected = async () => {
    if (!selectedIds.length) return
    const count = selectedIds.length
    if (!window.confirm(
      `把已选的 ${count} 个岗位批量放行到确认队列吗？\n\n只对「已过滤」状态的岗位生效——你的判断会覆盖 AI 评分；其他状态的岗位会被跳过并提示原因。`
    )) return
    try {
      const result = await postJobAction('/api/jobs/bulk-approve', { job_ids: selectedIds })
      setSelectedIds([])
      refreshJobs()
      const skippedCount = Array.isArray(result?.skipped) ? result.skipped.length : 0
      setNotice(
        `已放行 ${result?.approved_count ?? 0} 个岗位到确认队列${skippedCount ? `，跳过 ${skippedCount} 个（仅"已过滤"状态可放行）` : ''}。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '批量放行失败')
    }
  }

  // 收尾：跨页全选 + 筛选结果一键放行（预览评分分布）
  const filteredInResult = useMemo(() => items.filter(job => job.status === 'filtered').length, [items])

  const buildFilterParams = () => {
    const params = new URLSearchParams()
    if (filters.query.trim()) params.set('q', filters.query.trim())
    if (filters.minScore) params.set('min_score', filters.minScore)
    if (filters.salaryMin) params.set('salary_min', filters.salaryMin)
    if (filters.salaryMax) params.set('salary_max', filters.salaryMax)
    if (filters.status) params.set('status', filters.status)
    if (filters.createdWithin) params.set('created_within', filters.createdWithin)
    if (filters.sourcePlatform) params.set('source_platform', filters.sourcePlatform)
    if (filters.sourceChannel) params.set('source_channel', filters.sourceChannel)
    if (filters.education) params.set('education', filters.education)
    if (filters.recruitmentType) params.set('recruitment_type', filters.recruitmentType)
    params.set('sort_by', sortBy)
    params.set('sort_order', sortOrder)
    return params
  }

  // 后端 /api/jobs/search 单页上限 100，分页循环取全量（安全上限 2000 条）
  const fetchFilteredJobs = async (): Promise<Array<{ id: string; status: string; score: number }>> => {
    const all: Array<{ id: string; status: string; score: number }> = []
    const pageSize = 100
    for (let offset = 0; offset < 2000; offset += pageSize) {
      const params = buildFilterParams()
      params.set('limit', String(pageSize))
      params.set('offset', String(offset))
      const res = await fetch(`/api/jobs/search?${params.toString()}`, { cache: 'no-store' })
      if (!res.ok) throw new Error('获取筛选结果失败')
      const data = await res.json()
      const items = (data.items || []) as Array<{ id: string; status: string; score: number }>
      all.push(...items)
      if (items.length < pageSize) break
    }
    return all
  }

  const selectAllFiltered = async () => {
    try {
      const items = await fetchFilteredJobs()
      const ids = items.map(job => job.id)
      setSelectedIds(ids)
      setNotice(`已全选筛选结果 ${ids.length} 个岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '全选失败')
    }
  }

  const openApprovePreview = async () => {
    try {
      const allItems = await fetchFilteredJobs()
      const filteredJobs = allItems.filter(job => job.status === 'filtered')
      const scoreBuckets = [
        { label: '30 分以下', count: filteredJobs.filter(j => (j.score || 0) < 30).length },
        { label: '30-45 分', count: filteredJobs.filter(j => (j.score || 0) >= 30 && (j.score || 0) < 46).length },
        { label: '46 分以上', count: filteredJobs.filter(j => (j.score || 0) >= 46).length },
      ].filter(bucket => bucket.count > 0)
      setApprovePreview({
        filteredCount: filteredJobs.length,
        skippedCount: allItems.length - filteredJobs.length,
        scoreBuckets,
        ids: filteredJobs.map(job => job.id),
      })
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '获取预览失败')
    }
  }

  const confirmApprovePreview = async () => {
    if (!approvePreview || batchApproving) return
    setBatchApproving(true)
    try {
      const result = await postJobAction('/api/jobs/bulk-approve', { job_ids: approvePreview.ids })
      setSelectedIds([])
      setApprovePreview(null)
      refreshJobs()
      const skippedCount = Array.isArray(result?.skipped) ? result.skipped.length : 0
      setNotice(`已放行 ${result?.approved_count ?? 0} 个岗位到确认队列${skippedCount ? `，跳过 ${skippedCount} 个` : ''}。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '放行失败')
    } finally {
      setBatchApproving(false)
    }
  }

  const restoreJobs = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确认恢复 ${jobIds.length} 个岗位吗？恢复后不会自动评分或投递。`)) return
    try {
      const result = await postJobAction('/api/jobs/restore', { job_ids: jobIds, confirmed: true })
      setRecycleSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已恢复 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '恢复失败')
    }
  }

  const requestPermanentDelete = (jobIds: string[]) => {
    if (!jobIds.length) return
    setPermanentDeleteIds(jobIds)
    setPermanentDeleteAcknowledged(false)
  }

  const confirmPermanentDelete = async () => {
    if (!permanentDeleteIds.length || !permanentDeleteAcknowledged) return
    try {
      const result = await postJobAction('/api/jobs/permanent-delete', {
        job_ids: permanentDeleteIds,
        confirmed: true,
        confirmation: 'PERMANENT_DELETE',
      })
      setRecycleSelectedIds(previous => previous.filter(id => !permanentDeleteIds.includes(id)))
      setPermanentDeleteIds([])
      setPermanentDeleteAcknowledged(false)
      await loadRecycleBin()
      setNotice(`已永久删除 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '永久删除失败')
    }
  }

  const exportJobs = async (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => {
    try {
      const res = await fetch('/api/jobs/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          format,
          scope,
          job_ids: scope === 'selected' ? selectedIds : [],
          filters: scope === 'filtered' ? {
            q: filters.query.trim(),
            min_score: filters.minScore,
            salary_min: filters.salaryMin,
            salary_max: filters.salaryMax,
            status: filters.status,
            created_within: filters.createdWithin,
            source_platform: filters.sourcePlatform,
          } : {},
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '导出失败')
      }
      const blob = await res.blob()
      const disposition = res.headers.get('Content-Disposition') || ''
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `openjob-jobs.${format}`
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      window.URL.revokeObjectURL(url)
      const exportedCount = Number(res.headers.get('X-Exported-Count'))
      setNotice(`已导出 ${Number.isFinite(exportedCount) ? exportedCount : 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '导出失败')
    }
  }

  const startScoring = async (options: {
    scope: 'pending' | 'failed' | 'selected' | 'all_scored'
    limit: number | null
    job_ids: string[]
    force_rescore: boolean
  }) => {
    const res = await fetch('/api/scoring/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ options }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      const checks = Array.isArray(data.messages) ? data.messages.join('；') : ''
      throw new Error([data.error || '启动评分失败', checks].filter(Boolean).join('：'))
    }
    setNotice(`独立评分已启动，共 ${data.run?.remaining_job_ids?.length || 0} 个岗位。`)
  }

  const startQuickScoring = async () => {
    if (!window.confirm('将对岗位池中所有未评分或评分失败的岗位启动 AI 评分，可能产生模型费用，是否继续？')) return
    setQuickScoring(true)
    try {
      await startScoring({ scope: 'pending', limit: null, job_ids: [], force_rescore: false })
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '启动 AI 评分失败')
    } finally {
      setQuickScoring(false)
    }
  }

  // 放行预览弹窗：portal 提为变量，主视图与回收站两个分支都可渲染
  //（此前渲染在回收站分支早返回内，主视图点「放行筛选结果」看不到弹窗）
  const approvePreviewDialog = approvePreview && createPortal(
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm" onMouseDown={event => { if (event.target === event.currentTarget && !batchApproving) setApprovePreview(null) }}>
      <div role="dialog" aria-modal="true" aria-label="放行筛选结果确认" className="w-full max-w-lg rounded-3xl border border-card-border bg-card p-6 shadow-2xl">
        <h3 className="t-h2">批量放行筛选结果</h3>
        <ul className="mt-4 space-y-2 text-sm">
          <li>筛选结果共 <span className="font-semibold tabular-nums">{approvePreview.filteredCount + approvePreview.skippedCount}</span> 个岗位</li>
          <li>其中「已过滤」待放行：<span className="font-semibold text-primary tabular-nums">{approvePreview.filteredCount}</span> 个（你的判断覆盖 AI 评分）</li>
          <li>将被跳过（非已过滤状态）：<span className="tabular-nums">{approvePreview.skippedCount}</span> 个</li>
        </ul>
        {approvePreview.scoreBuckets.length > 0 && (
          <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-3">
            <div className="text-xs font-bold text-muted">放行岗位评分分布</div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {approvePreview.scoreBuckets.map(bucket => (
                <span key={bucket.label} className="rounded-full border border-card-border bg-card px-2.5 py-1">
                  {bucket.label}：<span className="font-semibold tabular-nums">{bucket.count}</span> 个
                </span>
              ))}
            </div>
            <p className="mt-2 text-xs leading-5 text-muted">低分岗位放行后仍走正常流程：生成招呼语 → 你确认 → 才发送。</p>
          </div>
        )}
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="secondary" size="sm" disabled={batchApproving} onClick={() => setApprovePreview(null)}>再看看</Button>
          <Button size="sm" disabled={batchApproving} onClick={() => void confirmApprovePreview()}>
            {batchApproving ? '放行中…' : `确认放行 ${approvePreview.filteredCount} 个岗位`}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  )

  if (showRecycleBin) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => setShowRecycleBin(false)}>返回岗位池</Button>
          <Button variant="secondary" size="sm" onClick={() => void loadRecycleBin()} disabled={recycleLoading}>刷新回收站</Button>
        </div>
        {notice && <div className="rounded-xl bg-accent-soft px-4 py-3 text-sm text-primary">{notice}</div>}
        <RecycleBinPanel
          jobs={recycleJobs}
          selectedIds={recycleSelectedIds}
          loading={recycleLoading}
          onToggleSelected={id => setRecycleSelectedIds(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])}
          onSelectAll={setRecycleSelectedIds}
          onRestore={ids => void restoreJobs(ids)}
          onPermanentDelete={requestPermanentDelete}
        />
        {approvePreviewDialog}
        {permanentDeleteIds.length > 0 && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true">
            <div className="w-full max-w-lg rounded-3xl border border-danger/30 bg-card p-6 shadow-2xl">
              <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-6 w-6 shrink-0 text-danger" /><div><h3 className="t-h2">确认永久删除</h3><p className="mt-2 text-sm leading-6 text-muted">将永久删除 {permanentDeleteIds.length} 条岗位及其历史，无法恢复。存在发送或回复证据的岗位会被后端拒绝删除。</p></div></div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-danger/20 bg-danger/10 p-3 text-sm font-bold"><input type="checkbox" checked={permanentDeleteAcknowledged} onChange={event => setPermanentDeleteAcknowledged(event.target.checked)} className="mt-0.5 h-4 w-4 accent-danger" /><span>我确认永久删除，并了解此操作无法撤销。</span></label>
              <div className="mt-6 flex justify-end gap-3"><Button variant="secondary" size="sm" onClick={() => setPermanentDeleteIds([])}>取消</Button><Button variant="destructive" size="sm" disabled={!permanentDeleteAcknowledged} onClick={() => void confirmPermanentDelete()}>永久删除</Button></div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-3xl border border-card-border bg-card p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="t-h1">岗位池</h2>
          <p className="mt-1 text-sm text-muted">集中查看已采集岗位、AI 分数、状态和详情入口。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => { setShowRecycleBin(true); void loadRecycleBin() }}><Trash2 className="mr-1 h-4 w-4" />回收站 ({recycleJobs.length})</Button>
          <BriefcaseBusiness className="h-6 w-6 text-primary" />
        </div>
      </div>
      <JobFilterBar
        filters={filters}
        onChange={setFilters}
        onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
        resultCount={total}
        totalCount={allTotal}
        invalidSalary={hasInvalidSalaryRange(filters)}
        showStatus
        showSource
      />
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
        {/* 选择组 */}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" disabled={!items.length} onClick={toggleCurrentPage}>
            {allPageSelected ? '取消选择本页' : '选择本页'}
          </Button>
          <Button variant="secondary" size="sm" disabled={!total} onClick={() => void selectAllFiltered()}>全选筛选结果 ({total})</Button>
          {selectedIds.length > 0 && <Button variant="ghost" size="sm" onClick={() => setSelectedIds([])}>清空选择</Button>}
        </div>
        <span className="rounded-full border border-card-border bg-card px-3 py-2 text-muted">筛选结果 <span className="font-bold text-foreground tabular-nums">{total}</span> 个 · 已过滤待放行 <span className="font-bold text-foreground tabular-nums">{filteredInResult}</span> 个</span>
        {/* 放行组：唯一主行动，仅选择后出现 */}
        {selectedIds.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-primary/30 bg-accent-soft/50 px-3 py-1.5">
            <span className="font-bold text-primary">已选 {selectedIds.length} 条</span>
            <Button size="sm" onClick={() => void bulkApproveSelected()}>放行到确认队列</Button>
            <Button size="sm" variant="secondary" disabled={!selectedIds.length} onClick={() => void deliverSelectedJobs()}>
              <Send className="mr-1 h-3.5 w-3.5" />BOSS 一键投递已选
            </Button>
            <Button variant="destructive" size="sm" onClick={() => void softDelete(selectedIds)}>移入回收站</Button>
          </div>
        )}
        {filteredInResult > 0 && !selectedIds.length && (
          <Button variant="secondary" size="sm" disabled={!total} onClick={() => void openApprovePreview()}>
            放行筛选结果（已过滤 {filteredInResult} 个，含评分预览）
          </Button>
        )}
        {/* 更多：低频分析/输出操作 */}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void startQuickScoring()} disabled={quickScoring || !total}>
            {quickScoring ? '评分中…' : '一键 AI 评分'}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setShowScoreDialog(true)}>评分选项</Button>
          <Button variant="ghost" size="sm" onClick={() => { setShowRecycleBin(true); void loadRecycleBin() }}><Trash2 className="mr-1 h-3.5 w-3.5" />回收站 ({recycleJobs.length})</Button>
          <ExportMenu onExport={exportJobs} hasSelection={selectedIds.length > 0} hasFiltered={total > 0} />
        </div>
      </div>
      {notice && <div className="mb-4 rounded-xl bg-accent-soft px-4 py-3 text-sm text-primary">{notice}</div>}
      {deliveryTask && (
        <div className="mb-4 rounded-2xl border border-card-border bg-surface-hover p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-semibold">投递队列</div>
              <p className="mt-1 text-xs text-muted">只展示已人工确认的 BOSS 发送任务；智联和 51job 不会进入此队列。</p>
            </div>
            <span className="rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-primary">
              {deliveryTask.status === 'running' ? '处理中' : deliveryTask.status === 'completed' ? '已完成' : deliveryTask.status === 'failed' ? '失败' : deliveryTask.status}
            </span>
          </div>
          <div className="mt-3 rounded-xl border border-card-border bg-card px-3 py-2 text-sm">
            <div className="font-bold">{deliveryTask.logs?.[deliveryTask.logs.length - 1] || '队列已创建，等待执行'}</div>
            <div className="mt-1 text-xs text-muted">任务 ID：{deliveryTask.id}</div>
          </div>
        </div>
      )}
      {error && <div className="mb-4 rounded-xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}
      <JobsTable
        jobs={items}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        selectedIds={selectedIds}
        onToggleSelected={toggleSelected}
        onSoftDelete={job => void softDelete([job.id])}
        onMarkManuallySent={job => void markManuallySent(job)}
        onApprove={job => void approveJob(job)}
        onDetail={job => setDetailJob(job)}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSortChange={changeSort}
      />
      {detailJob && <JobDetailModal job={detailJob} onClose={() => setDetailJob(null)} />}
      {approvePreviewDialog}
      <ScoreJobsDialog
        open={showScoreDialog}
        selectedJobIds={selectedIds}
        onClose={() => setShowScoreDialog(false)}
        onStart={startScoring}
      />
    </div>
  )
}


