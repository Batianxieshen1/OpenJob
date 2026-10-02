import { useState } from 'react'

/**
 * 公司头像：优先展示采集落盘的 BOSS Logo（/company-logos/ 静态路由），
 * 加载失败或未采集时回退"公司首字 + 主题色圆底"，两主题下均可读。
 * 独立 props 设计：岗位卡传 job 字段，统计页 top 公司榜也能复用。
 */
export function CompanyAvatar({
  company,
  logoPath,
  size = 20,
}: {
  company: string
  logoPath?: string | null
  size?: number
}) {
  const [failed, setFailed] = useState(false)
  const filename = logoPath ? logoPath.split('/').pop() : ''
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
  const initial = (company || '?').trim().charAt(0).toUpperCase() || '?'
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
