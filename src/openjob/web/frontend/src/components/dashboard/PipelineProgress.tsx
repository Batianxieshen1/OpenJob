import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface PipelineProgressProps {
  funnelToday: Record<string, number | undefined>
  pendingCount: number
}

interface Stage {
  label: string
  value: number
}

/** 求职流程进度条：采集 → 初筛 → AI 评分 → 人工确认 → 投递（数据来自今日漏斗） */
export function PipelineProgress({ funnelToday, pendingCount }: PipelineProgressProps) {
  const stages: Stage[] = [
    { label: '采集', value: funnelToday['采集总数'] || 0 },
    { label: '初筛', value: funnelToday['初筛通过'] || 0 },
    { label: 'AI 评分', value: funnelToday['AI评分'] || 0 },
    { label: '人工确认', value: funnelToday['发送'] || pendingCount },
    { label: '投递', value: funnelToday['发送'] || 0 },
  ]
  const total = stages.reduce((sum, s) => sum + s.value, 0)
  // 当前阶段判定：待确认积压 > 0 时卡在"人工确认"；否则为今日漏斗里第一个 0 值节点
  let currentIndex: number
  if ((funnelToday['发送'] || 0) > 0) {
    currentIndex = stages.length // 全部完成
  } else if (pendingCount > 0) {
    currentIndex = 3
  } else {
    currentIndex = Math.max(0, stages.findIndex(s => s.value === 0))
  }

  const overall = total === 0 ? 0 : Math.min(100, Math.round((stages.filter((s, i) => i < currentIndex).length / stages.length) * 100))

  return (
    <section className="flex min-h-[108px] flex-col justify-center rounded-module border border-card-border bg-card px-6 py-5 shadow-card">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-semibold text-muted">今日求职流程</h3>
        <span className="text-xs font-semibold text-primary tabular-nums">{overall}%</span>
      </div>

      <div className="relative mt-4">
        {/* 连接线 */}
        <div className="absolute left-0 right-0 top-[13px] h-0.5 rounded-full bg-card-border" />
        <div
          className="absolute left-0 top-[13px] h-0.5 rounded-full bg-primary transition-all duration-700"
          style={{ width: currentIndex <= 0 ? 0 : `${((currentIndex - 0.5) / (stages.length - 1)) * 100}%` }}
        />
        <ol className="relative flex justify-between">
          {stages.map((stage, i) => {
            const done = i < currentIndex
            const current = i === currentIndex && currentIndex < stages.length
            return (
              <li key={stage.label} className="group flex flex-col items-center gap-1.5">
                <span
                  className={cn(
                    'flex h-[26px] w-[26px] items-center justify-center rounded-full border-2 text-[10px] font-semibold transition-soft',
                    done && 'border-primary bg-primary text-white',
                    current && 'border-primary bg-shell text-primary breathe',
                    !done && !current && 'border-card-border bg-shell text-muted-3'
                  )}
                >
                  {done ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className={cn('text-[11px] leading-none', done || current ? 'font-semibold text-foreground' : 'text-muted-3')}>
                  {stage.label}
                </span>
                <span className="text-[10px] leading-none text-muted-3 tabular-nums" title={`${stage.label}今日 ${stage.value}`}>
                  {stage.value}
                </span>
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}
