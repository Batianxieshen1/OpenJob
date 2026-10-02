import { Star } from 'lucide-react'
import { CompanyAvatar } from '@/components/jobs/CompanyAvatar'
import type { Job } from '@/hooks/useDashboard'

/** 最佳匹配卡：今日评分最高的 1–3 个岗位，公司首字母圆形标做视觉焦点 */
export function BestMatchCard({ jobs }: { jobs: Job[] }) {
  const top = [...jobs].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 3)
  const best = top[0]

  return (
    <section className="flex min-h-[196px] flex-col rounded-module border border-card-border bg-card p-5 shadow-card">
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-semibold">最佳匹配</h3>
        <Star className="h-4 w-4 text-primary" />
      </div>

      {best ? (
        <>
          <div className="mt-3 flex items-center gap-3">
            <CompanyAvatar company={best.company} logoPath={best.company_logo_path} size={44} />
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{best.company}｜{best.title}</div>
              <div className="mt-0.5 truncate text-xs text-muted">{best.salary || '薪资面议'} · {best.city || '未知城市'}</div>
            </div>
            <span className="ml-auto shrink-0 text-2xl font-semibold text-primary tabular-nums">{best.score || '-'}</span>
          </div>

          {top.length > 1 && (
            <ul className="mt-3 space-y-1.5 border-t border-card-border pt-3">
              {top.slice(1).map(job => (
                <li key={job.id} className="flex items-center justify-between gap-2 text-xs">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <CompanyAvatar company={job.company} logoPath={job.company_logo_path} size={18} />
                    <span className="truncate text-muted">{job.company}｜{job.title}</span>
                  </span>
                  <span className="shrink-0 font-semibold text-primary tabular-nums">{job.score || '-'}</span>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button"
            onClick={() => { window.location.href = '/confirm' }}
            className="mt-auto pt-3 text-left text-xs font-semibold text-primary transition-soft hover:opacity-80"
          >
            去确认投递 →
          </button>
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted">还没有待确认岗位，先运行一次采集吧。</div>
      )}
    </section>
  )
}
