import { ArrowUpRight, FileWarning, Inbox, Send } from 'lucide-react'

interface ActionItemsCardProps {
  pendingCount: number
  needsResumeCount: number
  sendErrorsCount: number
  pendingRepliesCount: number
}

const tiles = [
  { key: 'confirm', label: '待确认投递', to: '/confirm', icon: Inbox },
  { key: 'resume', label: 'HR 要简历', to: '/monitor', icon: FileWarning },
  { key: 'errors', label: '发送失败', to: '/jobs', icon: Send },
] as const

/** 待办事项卡：三个数字 + 统一入口，替代旧的等规格 KPI 卡阵列 */
export function ActionItemsCard({ pendingCount, needsResumeCount, sendErrorsCount, pendingRepliesCount }: ActionItemsCardProps) {
  const values: Record<string, number> = {
    confirm: pendingCount,
    resume: needsResumeCount,
    errors: sendErrorsCount,
  }
  const attention = needsResumeCount + sendErrorsCount + pendingRepliesCount

  return (
    <section className="flex min-h-[196px] flex-col rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-semibold">需要你处理</h3>
        {attention > 0 && (
          <span className="rounded-full bg-warning/15 px-2.5 py-1 text-[11px] font-semibold text-warning tabular-nums">
            {attention} 项优先
          </span>
        )}
      </div>
      <div className="mt-3 grid flex-1 grid-cols-3 gap-2">
        {tiles.map(tile => {
          const value = values[tile.key]
          const hot = value > 0 && tile.key !== 'confirm'
          return (
            <button
              key={tile.key}
              type="button"
              onClick={() => { window.location.href = tile.to }}
              className={`group flex flex-col items-start justify-between rounded-2xl border p-3 text-left transition-soft hover:-translate-y-px ${
                hot ? 'border-warning/30 bg-warning/8' : 'border-card-border bg-surface-hover hover:border-primary/40'
              }`}
            >
              <tile.icon className={`h-4 w-4 ${hot ? 'text-warning' : 'text-muted'}`} />
              <span className="mt-2 text-2xl font-semibold tabular-nums text-foreground">{value}</span>
              <span className="text-[11px] text-muted">{tile.label}</span>
            </button>
          )
        })}
      </div>
      <button
        type="button"
        onClick={() => { window.location.href = '/confirm' }}
        className="mt-3 flex items-center justify-center gap-1 rounded-full border border-card-border py-2 text-xs font-semibold text-muted transition-soft hover:border-primary/40 hover:text-primary"
      >
        进入投递确认
        <ArrowUpRight className="h-3.5 w-3.5" />
      </button>
    </section>
  )
}
