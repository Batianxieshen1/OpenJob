import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Eye, ExternalLink, XCircle, CheckCircle2, History, MessageCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { parseUtc, formatUtcTime } from "@/lib/datetime"
import { getActionLabel, getStatusLabel } from '@/lib/status'
import type { Job } from '@/hooks/useDashboard'

export function jobSubtitle(job: Job) {
  const parts = [
    job.city || '城市未知',
    job.salary || '薪资面议',
    job.education || '学历未识别',
    job.recruitment_type === 'campus' ? '校招' : job.recruitment_type === 'experienced' ? '社招' : '',
  ]
  return parts.filter(Boolean).join(' · ')
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-card-border bg-surface-hover p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-semibold text-foreground">{value}</div>
    </div>
  )
}

interface TimelineItem {
  id: number
  action: string
  detail: string
  created_at: string
}

/** A1 回流摘要块：回复次数 + 最新摘要 + 手动兜底标记 */
function ReplySummaryBlock({ job }: { job: Job }) {
  const count = Number(job.reply_count || 0)
  return (
    <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm font-semibold">
          <MessageCircle className="mr-1.5 inline h-4 w-4 text-success" />
          HR 回复{count > 0 ? <span className="ml-1 text-success tabular-nums">×{count}</span> : <span className="ml-2 text-xs font-normal text-muted">暂无回复记录</span>}
        </div>
        {job.replied_at && <span className="text-xs text-muted">首响 {formatUtcTime(job.replied_at)}</span>}
      </div>
      {job.last_reply_snippet && (
        <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted">“{job.last_reply_snippet}”</p>
      )}
    </div>
  )
}

/** A2 事件时间线：岗位从采集到终态的全部动作，每步带时间戳与操作者 */
function HistoryTimeline({ jobId }: { jobId: string }) {
  const [items, setItems] = useState<TimelineItem[] | null>(null)
  useEffect(() => {
    let cancelled = false
    fetch(`/api/jobs/${jobId}`, { cache: 'no-store' })
      .then(res => (res.ok ? res.json() : Promise.reject(new Error('加载失败'))))
      .then((data: { history?: TimelineItem[] }) => { if (!cancelled) setItems(data.history || []) })
      .catch(() => { if (!cancelled) setItems([]) })
    return () => { cancelled = true }
  }, [jobId])
  if (!items) return <p className="mt-2 text-xs text-muted">时间线加载中…</p>
  if (!items.length) return <p className="mt-2 text-xs text-muted">暂无事件记录。</p>
  return (
    <ol className="mt-3 space-y-2.5">
      {items.map(item => (
        <li key={item.id} className="flex gap-3">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/70" aria-hidden />
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">
              {getActionLabel(item.action)}
              <span className="ml-2 text-xs font-normal text-muted tabular-nums">
                {formatUtcTime(item.created_at)}
              </span>
            </div>
            {item.detail && <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted">{item.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  )
}

/** A2 手动流转：按当前状态给出合法的下一步按钮，非法迁移由后端 409 拒绝 */
const TRANSITION_OPTIONS: Record<string, Array<{ status: string; label: string; reason?: boolean }>> = {
  replied: [
    { status: 'interview', label: '约面试' },
    { status: 'hr_rejected', label: 'HR 拒绝' },
    { status: 'closed', label: '关闭', reason: true },
  ],
  interview: [
    { status: 'offer', label: '拿 Offer' },
    { status: 'hr_rejected', label: 'HR 拒绝' },
    { status: 'closed', label: '关闭', reason: true },
  ],
  offer: [{ status: 'closed', label: '关闭', reason: true }],
  hr_rejected: [{ status: 'closed', label: '关闭', reason: true }],
  stale: [{ status: 'ready', label: '重新激活' }],
}

function TransitionButtons({ job, onDone }: { job: Job; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const options = TRANSITION_OPTIONS[job.status] || []
  if (!options.length) return null

  const transition = async (target: { status: string; label: string; reason?: boolean }) => {
    let reason = ''
    if (target.reason) {
      reason = window.prompt('关闭原因（可选，方便以后复盘）：', '') || ''
      if (reason === null) return
    }
    setBusy(true)
    setError('')
    try {
      const res = await fetch(`/api/jobs/${job.id}/transition`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: target.status, reason }),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(payload.error?.message || payload.error || '流转失败')
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '流转失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap gap-2">
        {options.map(option => (
          <Button key={option.status} variant="secondary" size="sm" disabled={busy} onClick={() => transition(option)}>
            {option.label}
          </Button>
        ))}
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </div>
  )
}

/** 福利核验：输入福利关键词，逐个到 JD 原文里找证据 */
function WelfareVerifyBlock({ jobId, jd }: { jobId: string; jd: string }) {
  const [welfare, setWelfare] = useState('')
  const [results, setResults] = useState<{ keyword: string; found: boolean; evidence: string }[] | null>(null)
  const [checking, setChecking] = useState(false)

  const verify = async () => {
    if (!welfare.trim()) return
    setChecking(true)
    try {
      const res = await fetch(`/api/jobs/${jobId}/verify-welfare?welfare=${encodeURIComponent(welfare.trim())}`)
      const data = await res.json()
      setResults(res.ok ? data.results : null)
    } catch {
      setResults(null)
    } finally {
      setChecking(false)
    }
  }

  if (!jd.trim()) return null
  return (
    <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-4">
      <div className="text-sm font-semibold">福利核验</div>
      <p className="mt-1 text-xs text-muted">标签可能不真实。输入福利关键词（逗号分隔），逐个到 JD 原文里找证据。</p>
      <div className="mt-2 flex gap-2">
        <input
          value={welfare}
          onChange={event => setWelfare(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') void verify() }}
          placeholder="如：五险一金,双休,转正"
          aria-label="福利关键词"
          className="h-9 min-w-0 flex-1 rounded-control border border-card-border bg-card px-3 text-sm text-foreground outline-none placeholder:text-muted focus:border-primary"
        />
        <Button variant="secondary" size="sm" onClick={() => void verify()} disabled={checking}>
          {checking ? '核验中…' : '核验'}
        </Button>
      </div>
      {results && (
        <ul className="mt-3 space-y-1.5 text-xs">
          {results.map(r => (
            <li key={r.keyword} className="leading-5">
              <span className={r.found ? 'font-semibold text-success' : 'font-semibold text-danger'}>
                {r.found ? '✓ 有据' : '✗ 无原文'}
              </span>
              <span className="text-foreground"> {r.keyword}</span>
              {r.found && <span className="text-muted">｜…{r.evidence}…</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

interface JobDetailModalProps {
  job: Job
  onClose: () => void
}

/** 岗位详情弹窗：Portal 到 body，Esc/遮罩关闭，打开锁焦点、关闭归还焦点 */
export function JobDetailModal({ job, onClose }: JobDetailModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null
    const panel = panelRef.current
    panel?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = overflow
      restoreRef.current?.focus?.()
    }
  }, [onClose])

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm sm:p-6"
      onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`岗位详情：${job.company} ${job.title}`}
        tabIndex={-1}
        className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-overlay border border-card-border bg-card p-6 shadow-pop outline-none"
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold tracking-[0.18em] text-primary">岗位详情</div>
            <h3 className="mt-1 text-xl font-semibold md:text-2xl">{job.company}｜{job.title}</h3>
            <p className="mt-1 text-sm text-muted">{job.salary || '薪资未填'} · {job.city || '城市未填'} · {getStatusLabel(job.status)}</p>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>关闭</Button>
        </div>
        <div className="grid gap-3 text-sm lg:grid-cols-2">
          <InfoBlock label="HR" value={[job.hr_name, job.hr_title].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="招聘者活跃" value={job.hr_active || '活跃度未知'} />
          <InfoBlock label="公司" value={[job.company_size, job.company_industry].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="来源平台" value={[job.source_platform === 'zhilian' ? '智联招聘｜当前只开放采集' : job.source_platform === '51job' ? '前程无忧｜当前只开放采集' : 'BOSS 直聘', ...(Array.isArray(job.source_labels) && job.source_labels.length ? [`来源：${job.source_labels.join(' + ')}`] : job.source_channel === 'recommendation' ? ['来源：推荐页'] : [])].filter(Boolean).join(' · ')} />
          <InfoBlock label="匹配分" value={String(job.score || '-')} />
          <InfoBlock label="定制简历" value={job.resume_path || '未生成'} />
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-4">
          <div className="text-sm font-semibold">评分理由</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.score_reason || '-'}</p>
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-4">
          <div className="text-sm font-semibold">招呼语</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.greeting || '未生成'}</p>
        </div>
        <ReplySummaryBlock job={job} />
        <TransitionButtons job={job} onDone={onClose} />
        <div className="mt-4 rounded-2xl border border-card-border bg-surface-hover p-4">
          <div className="flex items-center text-sm font-semibold">
            <History className="mr-1.5 h-4 w-4 text-primary" />
            事件时间线
          </div>
          <HistoryTimeline jobId={job.id} />
        </div>
        <WelfareVerifyBlock jobId={job.id} jd={job.jd || ''} />
      </div>
    </div>,
    document.body
  )
}

interface JobActionCardProps {
  job: Job
  selected: boolean
  onToggle: () => void
  onDetail: () => void
  onReject: () => void
}

/** B9 队列时效：审批后在确认队列等待的天数 */
export function waitingDays(job: Job): number {
  const base = job.updated_at || job.created_at
  if (!base) return 0
  const parsed = parseUtc(base)
  if (!parsed) return 0
  const ms = Date.now() - parsed.getTime()
  if (!Number.isFinite(ms) || ms <= 0) return 0
  return Math.floor(ms / 86_400_000)
}

/** 待确认岗位卡：选中态用描边+浅蓝勾角标表达，不做整卡高饱和；超 7 天灰显建议重检 */
export function JobActionCard({ job, selected, onToggle, onDetail, onReject }: JobActionCardProps) {
  const days = waitingDays(job)
  const overdue = days > 7
  return (
    <div
      className={`relative rounded-card border p-4 transition-soft ${
        overdue && !selected
          ? 'border-card-border bg-surface-hover opacity-75'
          : selected
            ? 'border-primary bg-accent-soft/40'
            : 'border-card-border bg-card hover:border-primary/30'
      }`}
    >
      {selected && (
        <span aria-hidden className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-white shadow-pop">
          <CheckCircle2 className="h-3.5 w-3.5" />
        </span>
      )}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold text-foreground">{job.company}</span>
            <span className="shrink-0 text-muted-3">｜</span>
            <span className="truncate font-semibold text-foreground">{job.title}</span>
          </div>
          <div className="mt-1 text-xs text-muted">{jobSubtitle(job)}</div>
        </div>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`选择岗位：${job.company} ${job.title}`}
          className="mt-1 h-4 w-4 shrink-0 accent-primary"
        />
      </div>
      {days >= 2 && (
        <p className={`mt-2 text-xs ${overdue ? 'font-semibold text-warning' : 'text-muted'}`}>
          已等待 {days} 天{overdue ? '，超过 7 天：投递价值可能下降，建议重检评分或过期退出' : ''}
        </p>
      )}
      <p className="mt-3 line-clamp-2 min-h-12 text-[13px] leading-6 text-muted">{job.score_reason || job.greeting || '等待继续推进。'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={onDetail}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
        <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />跳转岗位链接</Button>
        <Button variant="secondary" size="sm" onClick={onReject}><XCircle className="mr-2 h-4 w-4" />放弃岗位</Button>
      </div>
    </div>
  )
}

/** 紧凑优先事项行：公司/职位 + 分数/薪资/活跃 + 单一主操作 */
export function PriorityJobRow({
  job,
  badge,
  action,
}: {
  job: Job
  badge: string
  action: { label: string; onClick: () => void }
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-card-border bg-card px-4 py-3 transition-soft hover:border-primary/30">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-foreground">{job.company}｜{job.title}</div>
        <div className="mt-0.5 truncate text-xs text-muted">
          {job.score ? <span className="font-semibold text-primary tabular-nums">{job.score} 分</span> : null}
          {job.salary ? ` · ${job.salary}` : ''}
          {job.hr_active ? ` · ${job.hr_active}` : ''}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <span className="hidden rounded-full bg-accent-soft px-2 py-1 text-[11px] font-semibold text-primary sm:inline">{badge}</span>
        <Button size="sm" onClick={action.onClick}>{action.label}</Button>
      </div>
    </div>
  )
}
