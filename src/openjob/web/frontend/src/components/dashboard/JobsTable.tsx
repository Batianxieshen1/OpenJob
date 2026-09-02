import { Fragment, useEffect, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { CheckCircle2, ChevronDown, ChevronUp, ExternalLink, Trash2 } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import { cn } from '@/lib/utils'
import type { Job } from '@/hooks/useDashboard'
import type { JobSortKey, JobSortOrder } from '@/hooks/useJobSearch'

interface JobsTableProps {
  jobs: Job[]
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onSoftDelete?: (job: Job) => void
  onMarkManuallySent?: (job: Job) => void
  onApprove?: (job: Job) => void
  loading?: boolean
  sortBy: JobSortKey
  sortOrder: JobSortOrder
  onSortChange: (sortBy: JobSortKey) => void
}

function safeExternalJobUrl(job: Job): string | null {
  if (job.source_platform !== 'zhilian' && job.source_platform !== '51job') return null
  try {
    const parsed = new URL(job.url || '')
    if (parsed.protocol !== 'https:') return null
    const rootDomain = job.source_platform === 'zhilian' ? 'zhaopin.com' : '51job.com'
    if (parsed.hostname !== rootDomain && !parsed.hostname.endsWith(`.${rootDomain}`)) return null
    return parsed.toString()
  } catch {
    return null
  }
}

function statusVariant(status: string) {
  const variants = new Set([
    'pending',
    'scored',
    'filtered',
    'ready',
    'approved',
    'skipped',
    'sent',
    'replied',
    'resume_sent',
    'needs_resume',
    'follow_up_sent',
    'rejected',
    'error',
  ])
  return variants.has(status) ? status : 'default'
}

function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 80
    ? 'border-success/40 bg-success/10 text-success'
    : score >= 60
      ? 'border-primary/35 bg-accent-soft text-primary'
      : 'border-card-border bg-surface-hover text-muted'
  return (
    <span
      className={cn(
        'inline-flex h-10 w-10 items-center justify-center rounded-full border-2 text-sm font-semibold tabular-nums',
        tone
      )}
      title={`匹配分 ${score}`}
    >
      {score || '-'}
    </span>
  )
}

export function JobsTable({ jobs, page, pageSize, total, onPageChange, selectedIds, onToggleSelected, onSoftDelete, onMarkManuallySent, onApprove, loading = false, sortBy, sortOrder, onSortChange }: JobsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pageInput, setPageInput] = useState(String(page + 1))
  const totalPages = Math.ceil(total / pageSize)
  const hasActions = Boolean(onSoftDelete || onMarkManuallySent)

  useEffect(() => {
    setPageInput(String(page + 1))
  }, [page])

  const jumpToPage = () => {
    const requested = Number.parseInt(pageInput, 10)
    if (!Number.isFinite(requested) || totalPages < 1) {
      setPageInput(String(page + 1))
      return
    }
    onPageChange(Math.min(totalPages - 1, Math.max(0, requested - 1)))
  }

  const timeAgo = (dateStr: string) => {
    if (!dateStr) return ''
    const normalizedDate = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(dateStr)
      ? `${dateStr.replace(' ', 'T')}Z`
      : dateStr
    const timestamp = new Date(normalizedDate).getTime()
    if (Number.isNaN(timestamp)) return ''
    const diff = Date.now() - timestamp
    const hours = Math.floor(diff / 3600000)
    if (hours < 1) return '刚刚'
    if (hours < 24) return `${hours}h 前`
    return `${Math.floor(hours / 24)}d 前`
  }

  const sortableHeader = (label: string, key: JobSortKey) => (
    <button
      type="button"
      onClick={() => onSortChange(key)}
      className="inline-flex items-center gap-1 font-semibold hover:text-primary"
      title={`按${label}排序`}
    >
      {label}<span className="text-[10px]">{sortBy === key ? (sortOrder === 'asc' ? '↑' : '↓') : '↕'}</span>
    </button>
  )

  // 列数：选/岗位/城市/薪资/评分/状态/时间/操作（响应式隐藏列不计入 colSpan 上限）
  const columnCount = hasActions ? 8 : 7

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>岗位列表</CardTitle>
        <span className="text-xs text-muted">{total} 条记录</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead>
              <tr className="border-b border-card-border bg-accent-soft text-xs text-muted">
                <th className="w-10 px-3 py-3 text-center font-semibold">选</th>
                <th className="px-4 py-3 text-left font-semibold">岗位</th>
                <th className="hidden w-24 px-4 py-3 text-left font-semibold md:table-cell">城市</th>
                <th className="px-4 py-3 text-left font-semibold">{sortableHeader('薪资', 'salary')}</th>
                <th className="w-16 whitespace-nowrap px-4 py-3 text-center font-semibold">{sortableHeader('评分', 'score')}</th>
                <th className="whitespace-nowrap px-4 py-3 text-left font-semibold">{sortableHeader('状态', 'status')}</th>
                <th className="hidden w-20 px-4 py-3 text-left font-semibold lg:table-cell">{sortableHeader('时间', 'created_at')}</th>
                {hasActions && <th className="min-w-[200px] px-3 py-3 text-center font-semibold">操作</th>}
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => {
                const isExpanded = expanded === job.id
                const isExternalPlatform = job.source_platform === 'zhilian' || job.source_platform === '51job'
                const externalUrl = safeExternalJobUrl(job)
                const alreadySent = ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'].includes(job.status)
                const subtitleParts = [
                  job.education || '学历未识别',
                  job.recruitment_type === 'campus' ? '校招' : job.recruitment_type === 'experienced' ? '社招' : '',
                  job.hr_active || '活跃度未知',
                ].filter(Boolean)
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="cursor-pointer border-b border-card-border bg-card transition-colors hover:bg-surface-hover"
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      <td className="px-3 py-3 text-center align-middle" onClick={event => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(job.id)}
                          onChange={() => onToggleSelected(job.id)}
                          aria-label={`选择 ${job.company} ${job.title}`}
                          className="h-4 w-4 accent-primary"
                        />
                      </td>
                      <td className="max-w-[380px] px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-semibold text-foreground">{job.company}</span>
                          <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold text-primary">
                            {job.source_platform === 'zhilian' ? '智联' : job.source_platform === '51job' ? '51job' : 'BOSS'}
                          </span>
                        </div>
                        <div className="mt-0.5 flex min-w-0 items-center gap-2">
                          <span className="truncate text-[13px] text-foreground">{job.title}</span>
                          <span className="hidden shrink-0 text-[11px] text-muted-3 md:inline">
                            {subtitleParts.join(' · ')}
                          </span>
                        </div>
                      </td>
                      <td className="hidden whitespace-nowrap px-4 py-3 align-middle text-muted md:table-cell">{job.city || '未识别'}</td>
                      <td className="whitespace-nowrap px-4 py-3 align-middle text-muted">{job.salary || '-'}</td>
                      <td className="px-4 py-3 text-center align-middle">
                        <ScoreBadge score={job.score || 0} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 align-middle">
                        <Badge variant={statusVariant(job.status) as any}>{getStatusLabel(job.status)}</Badge>
                      </td>
                      <td className="hidden whitespace-nowrap px-4 py-3 align-middle text-xs text-muted lg:table-cell">
                        <span className="inline-flex items-center gap-1.5">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </span>
                      </td>
                      {hasActions && (
                        <td className="px-3 py-3 align-middle" onClick={event => event.stopPropagation()}>
                          <div className="flex flex-wrap items-center justify-center gap-1.5">
                            {isExternalPlatform && externalUrl && (
                              <a href={externalUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-semibold text-primary hover:bg-accent-soft">
                                <ExternalLink className="h-3.5 w-3.5" />打开平台
                              </a>
                            )}
                            {isExternalPlatform && !externalUrl && (
                              <span className="rounded-lg bg-warning/10 px-2 py-1.5 text-[11px] font-semibold text-warning">链接不可用</span>
                            )}
                            {isExternalPlatform && onMarkManuallySent && (
                              <button
                                type="button"
                                disabled={alreadySent}
                                onClick={() => onMarkManuallySent(job)}
                                className="inline-flex items-center gap-1 rounded-lg bg-primary px-2 py-1.5 text-[11px] font-semibold text-white hover:opacity-90 disabled:bg-success/10 disabled:text-success disabled:opacity-100"
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" />{alreadySent ? '已发送' : '我已发送'}
                              </button>
                            )}
                            {job.status === 'filtered' && onApprove && (
                              <button
                                type="button"
                                onClick={() => onApprove(job)}
                                className="inline-flex items-center gap-1 rounded-lg border border-primary/40 px-2 py-1.5 text-[11px] font-semibold text-primary hover:bg-accent-soft"
                              >放行到确认队列</button>
                            )}
                            {onSoftDelete && (
                              <button type="button" onClick={() => onSoftDelete(job)} className="rounded-lg p-2 text-muted hover:bg-danger/10 hover:text-danger" aria-label={`将 ${job.company} ${job.title} 移入回收站`}>
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-surface-hover">
                        <td colSpan={columnCount} className="px-6 py-4">
                          <div className="grid grid-cols-1 gap-4 text-sm lg:grid-cols-3">
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-semibold text-primary">JD摘要</p>
                              <p className="line-clamp-6 leading-6 text-muted">{job.jd || '无'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-semibold text-primary">招呼语</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.greeting || '未生成'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-card p-4">
                              <p className="mb-2 text-xs font-semibold text-primary">评分理由</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.score_reason || '无'}</p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!jobs.length && (
                <tr>
                  <td colSpan={columnCount} className="px-4 py-10 text-center text-sm text-muted">
                    {loading ? '正在读取岗位…' : '没有符合当前条件的岗位'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-3 border-t border-card-border px-4 py-3 text-xs">
            <button
              onClick={() => onPageChange(0)}
              disabled={page === 0}
              className="font-semibold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              首页
            </button>
            <button
              onClick={() => onPageChange(Math.max(0, page - 1))}
              disabled={page === 0}
              className="font-semibold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              上一页
            </button>
            <label className="flex items-center gap-1 text-muted">
              第
              <input
                type="number"
                min={1}
                max={Math.max(1, totalPages)}
                value={pageInput}
                onChange={event => setPageInput(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') jumpToPage() }}
                onBlur={jumpToPage}
                aria-label="跳转页码"
                className="w-14 rounded-control border border-card-border bg-surface-hover px-2 py-1 text-center text-foreground outline-none focus:border-primary"
              />
              页 / {totalPages} 页
            </label>
            <button
              onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="font-semibold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              下一页
            </button>
            <button
              onClick={() => onPageChange(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="font-semibold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              尾页
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
