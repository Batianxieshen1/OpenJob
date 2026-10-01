import { useState } from 'react'
import type { Job } from '@/hooks/useDashboard'

/**
 * 公司头像：优先展示采集落盘的 BOSS Logo（/company-logos/ 静态路由），
 * 加载失败或未采集时回退"公司首字 + 主题色圆底"，两主题下均可读。
 */
export function CompanyAvatar({ job, size = 20 }: { job: Job; size?: number }) {
  const [failed, setFailed] = useState(false)
  const filename = job.company_logo_path ? job.company_logo_path.split('/').pop() : ''
  const src = filename ? `/company-logos/${filename}` : ''
  if (src && !failed) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden
        width={size}
        height={size}
        onError={() => setFailed(true)}
        className={`shrink-0 border border-card-border bg-card object-cover ${size >= 28 ? 'rounded-lg' : 'rounded-md'}`}
      />
    )
  }
  const initial = (job.company || '?').trim().charAt(0).toUpperCase() || '?'
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.45)) }}
      className={`flex shrink-0 items-center justify-center bg-primary/12 font-semibold text-primary ${size >= 28 ? 'rounded-lg' : 'rounded-md'}`}
    >
      {initial}
    </span>
  )
}
