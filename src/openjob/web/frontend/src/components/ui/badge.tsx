import { cn } from '@/lib/utils'
import { cva, type VariantProps } from 'class-variance-authority'
import { HTMLAttributes } from 'react'

const badgeVariants = cva(
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border',
  {
    variants: {
      variant: {
        default: 'bg-muted/15 text-foreground/80 border-border',
        pending: 'bg-muted/10 text-muted border-border',
        scored: 'bg-primary/10 text-primary border-primary/20',
        ready: 'bg-primary/10 text-primary border-primary/25',
        approved: 'bg-warning/10 text-warning border-warning/20',
        skipped: 'bg-muted/10 text-muted border-border/70',
        sent: 'bg-success/10 text-success border-success/20',
        replied: 'bg-success/10 text-success border-success/20',
        resume_sent: 'bg-primary/10 text-primary border-primary/20',
        needs_resume: 'bg-warning/10 text-warning border-warning/20',
        follow_up_sent: 'bg-primary/10 text-primary border-primary/20',
        reply_pending: 'bg-warning/10 text-warning border-warning/20',
        auto_replied: 'bg-success/10 text-success border-success/20',
        rejected: 'bg-danger/10 text-danger border-danger/20',
        error: 'bg-danger/10 text-danger border-danger/20',
        filtered: 'bg-muted/10 text-muted border-border/70',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}
