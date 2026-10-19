import { useState } from 'react'

/**
 * HR 头像：优先展示采集落盘的 BOSS 头像（/hr-avatars/ 静态路由），
 * 加载失败或未采集时回退"姓名首字 + 中性圆底"。键 = 公司+姓名哈希（S0：无 boss_id）。
 */
export function HrAvatar({
  company,
  hrName,
  avatarPath,
  size = 16,
}: {
  company: string
  hrName: string
  avatarPath?: string | null
  size?: number
}) {
  const [failed, setFailed] = useState(false)
  const filename = avatarPath ? avatarPath.split('/').pop() : ''
  const src = filename ? `/hr-avatars/${filename}` : ''
  if (src && !failed) {
    return (
      <img
        src={src}
        alt=""
        aria-hidden
        width={size}
        height={size}
        onError={() => setFailed(true)}
        className="shrink-0 rounded-full border border-card-border bg-card object-cover"
      />
    )
  }
  const initial = (hrName || '?').trim().charAt(0) || '?'
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.5)) }}
      className="flex shrink-0 items-center justify-center rounded-full bg-surface-hover font-semibold text-muted"
    >
      {initial}
    </span>
  )
}
