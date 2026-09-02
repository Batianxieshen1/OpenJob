import { useState } from 'react'
import { ClipboardList } from 'lucide-react'
import { PriorityJobRow } from '@/components/jobs/JobCards'
import type { Job } from '@/hooks/useDashboard'

interface PriorityItemsCardProps {
  needsResume: Job[]
  sendErrors: Job[]
  topPending: Job[]
  onNotice: (message: string) => void
  onRefresh: () => void
}

type PriorityRow = {
  key: string
  job: Job
  badge: string
  label: string
  onClick: () => void
}

/** 优先事项列表：HR 要简历 > 发送失败 > 最高分待确认，每行只有一个主操作 */
export function PriorityItemsCard({ needsResume, sendErrors, topPending, onNotice, onRefresh }: PriorityItemsCardProps) {
  const [busyJobId, setBusyJobId] = useState<string | null>(null)

  const rows: PriorityRow[] = []

  for (const job of needsResume.slice(0, 2)) {
    rows.push({
      key: `resume-${job.id}`,
      job,
      badge: 'HR 要简历',
      label: '下载简历',
      onClick: () => window.open(`/api/jobs/${job.id}/resume/download`, '_blank'),
    })
  }
  for (const job of sendErrors.slice(0, 2)) {
    rows.push({
      key: `retry-${job.id}`,
      job,
      badge: '发送失败',
      label: '重试发送',
      onClick: () => {
        if (busyJobId) return
        setBusyJobId(job.id)
        fetch('/api/workbench/deliver', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_ids: [job.id], direct_send: true }),
        })
          .then(async res => {
            if (!res.ok) {
              const data = await res.json().catch(() => ({}))
              throw new Error(data.error || '发送失败')
            }
            onNotice(`已重新发起「${job.company}｜${job.title}」的发送。`)
            onRefresh()
          })
          .catch((err: unknown) => onNotice(err instanceof Error ? err.message : '发送失败'))
          .finally(() => setBusyJobId(null))
      },
    })
  }
  for (const job of [...topPending].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 3)) {
    rows.push({
      key: `confirm-${job.id}`,
      job,
      badge: `${job.score || '-'} 分`,
      label: '去确认',
      onClick: () => { window.location.href = '/confirm' },
    })
  }

  return (
    <section className="rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-primary" />
          <h3 className="text-[15px] font-semibold">今天最值得处理的</h3>
        </div>
        <button
          type="button"
          onClick={() => { window.location.href = '/confirm' }}
          className="text-xs font-semibold text-muted transition-soft hover:text-primary"
        >
          全部事项 →
        </button>
      </div>

      {rows.length ? (
        <div className="stagger space-y-2">
          {rows.map(row => (
            <PriorityJobRow
              key={row.key}
              job={row.job}
              badge={row.badge}
              action={{ label: busyJobId === row.job.id ? '发送中…' : row.label, onClick: row.onClick }}
            />
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-card-border bg-surface-hover px-5 py-8 text-center">
          <p className="text-sm text-foreground">今天没有需要处理的事项 🎉</p>
          <p className="mt-1 text-xs text-muted">可以运行一次采集，为明天储备新岗位。</p>
        </div>
      )}
    </section>
  )
}
