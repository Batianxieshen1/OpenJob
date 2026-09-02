import type { ComponentType, ReactNode } from 'react'
import type { LucideProps } from 'lucide-react'

interface EmptyStateProps {
  icon: ComponentType<LucideProps>
  title: string
  description: string
  steps?: string[]
  action?: ReactNode
}

/** 统一空状态：图形符号 + 状态 + 下一步 + 主操作 */
export function EmptyState({ icon: Icon, title, description, steps, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-module border border-dashed border-card-border bg-surface-hover px-6 py-12 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-soft">
        <Icon className="h-7 w-7 text-primary" strokeWidth={1.6} />
      </div>
      <h3 className="mt-4 text-[15px] font-semibold text-foreground">{title}</h3>
      <p className="mt-1 max-w-sm text-[13px] leading-6 text-muted">{description}</p>
      {steps && steps.length > 0 && (
        <ol className="mt-4 flex flex-wrap items-center justify-center gap-1.5 text-[11px] text-muted">
          {steps.map((step, i) => (
            <li key={step} className="flex items-center gap-1.5">
              {i > 0 && <span className="text-muted-3">→</span>}
              <span className="rounded-full bg-card px-2.5 py-1">{step}</span>
            </li>
          ))}
        </ol>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
