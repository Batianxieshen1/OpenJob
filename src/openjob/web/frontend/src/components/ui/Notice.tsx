import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

type NoticeTone = 'info' | 'success' | 'error'

/** 从通知文本推断语气：失败类→error，完成类→success，其余 info。
 *  让既有 setNotice(字符串) 的调用方零改动获得双色反馈。 */
function inferTone(text: string): NoticeTone {
  if (/失败|错误|无法|请先|不能/.test(text)) return 'error'
  if (/^已|^成功|完成|已放行|已生成|已发送|已删除|已恢复|已全选|已导出|已保存/.test(text)) return 'success'
  return 'info'
}

const tones: Record<NoticeTone, string> = {
  info: 'border-card-border bg-accent-soft/60 text-foreground',
  success: 'border-success/25 bg-success/10 text-success',
  error: 'border-danger/25 bg-danger/10 text-danger',
}

export function Notice({
  children,
  text,
  tone,
  className,
}: {
  children?: ReactNode
  /** 便捷用法：直接传通知字符串，自动推断语气 */
  text?: string
  tone?: NoticeTone
  className?: string
}) {
  const body = children ?? text ?? ''
  const resolved = tone ?? inferTone(String(body))
  return (
    <div
      role="status"
      className={cn('rise-in rounded-card border px-4 py-3 text-sm', tones[resolved], className)}
    >
      {body}
    </div>
  )
}
