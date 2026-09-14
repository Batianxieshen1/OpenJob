import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CheckCheck, ClipboardCopy, Inbox, Link2, Sparkles, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Conversation {
  id: number
  platform: string
  hr_name: string
  company: string
  last_message_snippet: string
  job_id: string | null
  match_status: 'matched' | 'ambiguous' | 'unmatched' | string
  handle_status: string
  updated_at: string
  job_title?: string | null
  job_status?: string | null
}

interface DraftState {
  draft: string | null
  fact_status: string
  issues: string[]
  injection_risks: string[]
}

function matchBadge(status: string) {
  if (status === 'matched') return { label: '已关联', variant: 'success' as const }
  if (status === 'ambiguous') return { label: '歧义（多个候选岗位）', variant: 'warning' as const }
  return { label: '未匹配，需人工关联', variant: 'warning' as const }
}

function formatTime(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return time.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

/** A3 回复工作台：聚合待处理会话，草稿只复制到剪贴板，绝不自动发送 */
export default function InboxPage() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [unresolvedReplies, setUnresolvedReplies] = useState(0)
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [drafts, setDrafts] = useState<Record<number, DraftState>>({})
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [linkTarget, setLinkTarget] = useState<Conversation | null>(null)
  const [linkJobId, setLinkJobId] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/inbox', { cache: 'no-store' })
      const payload = await res.json()
      setConversations(payload?.data?.conversations || [])
      setUnresolvedReplies(Number(payload?.data?.unresolved_replies) || 0)
    } catch {
      setNotice('无法读取收件箱，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const generateDraft = async (conv: Conversation) => {
    setBusyId(conv.id)
    setNotice('')
    try {
      const res = await fetch(`/api/conversations/${conv.id}/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const payload = await res.json()
      if (!res.ok) throw new Error(payload.error?.message || payload.error || '草稿生成失败')
      const data = payload.data as DraftState
      setDrafts(prev => ({ ...prev, [conv.id]: data }))
      if (data.issues?.length) {
        setNotice(`草稿已生成但未通过事实校验：${data.issues[0]}（请把这些经历补进素材库）`)
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '草稿生成失败')
    } finally {
      setBusyId(null)
    }
  }

  const copyDraft = async (conv: Conversation) => {
    const draft = drafts[conv.id]?.draft
    if (!draft) return
    try {
      await navigator.clipboard.writeText(draft)
      setCopiedId(conv.id)
      setTimeout(() => setCopiedId(null), 2000)
    } catch {
      setNotice('复制失败：请手动选中文本复制。')
    }
  }

  const resolve = async (conv: Conversation) => {
    setBusyId(conv.id)
    try {
      await fetch(`/api/conversations/${conv.id}/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const dismiss = async (conv: Conversation) => {
    setBusyId(conv.id)
    try {
      await fetch(`/api/conversations/${conv.id}/dismiss`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      await load()
    } finally {
      setBusyId(null)
    }
  }

  const link = async () => {
    if (!linkTarget || !linkJobId.trim()) return
    setBusyId(linkTarget.id)
    try {
      const res = await fetch(`/api/conversations/${linkTarget.id}/link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: linkJobId.trim() }),
      })
      const payload = await res.json()
      if (!res.ok) throw new Error(payload.error?.message || payload.error || '关联失败')
      setLinkTarget(null)
      setLinkJobId('')
      await load()
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '关联失败')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-4">
      <header>
        <h1 className="text-lg font-semibold">回复工作台</h1>
        <p className="text-xs text-muted">
          HR 回复聚合在这里：监测自动回流 + 未匹配会话人工关联。草稿只生成文本供你复制到平台发送——OpenJob 永远不替你自动回复。
          另有 <span className="font-semibold text-primary tabular-nums">{unresolvedReplies}</span> 条历史待确认回复在「监测执行」页。
        </p>
      </header>

      {notice && <div className="rise-in rounded-card border border-card-border bg-card px-4 py-3 text-sm text-foreground">{notice}</div>}

      {loading ? (
        <div className="flex h-40 items-center justify-center text-sm text-muted">加载中...</div>
      ) : conversations.length === 0 ? (
        <div className="rounded-module border border-dashed border-card-border bg-card p-10 text-center">
          <Inbox className="mx-auto h-10 w-10 text-muted-3" />
          <p className="mt-3 text-sm text-muted">暂无待处理会话。HR 回复后，监测会在下一个周期把它带到这里。</p>
          <Button className="mt-4" variant="secondary" size="sm" onClick={load}>刷新</Button>
        </div>
      ) : (
        <section className="space-y-3">
          {conversations.map(conv => {
            const badge = matchBadge(conv.match_status)
            const draft = drafts[conv.id]
            return (
              <article key={conv.id} className="rounded-card border border-card-border bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-foreground">{conv.company || '未知公司'}</span>
                      {conv.hr_name && <span className="text-sm text-muted">{conv.hr_name}</span>}
                      <Badge variant={badge.variant as any}>{badge.label}</Badge>
                      {conv.job_id && <span className="text-xs text-muted">岗位：{conv.job_title || conv.job_id}</span>}
                    </div>
                    <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted">“{conv.last_message_snippet || '（无消息摘要）'}”</p>
                    <p className="mt-1 text-xs text-muted tabular-nums">{formatTime(conv.updated_at)} · {conv.platform === 'boss' ? 'BOSS直聘' : conv.platform}</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {conv.job_id ? (
                      <>
                        <Button variant="secondary" size="sm" disabled={busyId === conv.id} onClick={() => generateDraft(conv)}>
                          <Sparkles className="mr-2 h-4 w-4" />{busyId === conv.id ? '生成中…' : '生成回复草稿'}
                        </Button>
                        <Button variant="secondary" size="sm" disabled={busyId === conv.id} onClick={() => resolve(conv)}>
                          <CheckCheck className="mr-2 h-4 w-4" />标记已处理
                        </Button>
                      </>
                    ) : (
                      <Button variant="secondary" size="sm" disabled={busyId === conv.id} onClick={() => { setLinkTarget(conv); setLinkJobId(''); setNotice('') }}>
                        <Link2 className="mr-2 h-4 w-4" />关联岗位
                      </Button>
                    )}
                    <Button variant="secondary" size="sm" disabled={busyId === conv.id} onClick={() => dismiss(conv)}>
                      <XCircle className="mr-2 h-4 w-4" />忽略
                    </Button>
                  </div>
                </div>

                {linkTarget?.id === conv.id && (
                  <div className="mt-3 rounded-xl border border-card-border bg-surface-hover p-3">
                    <label className="text-xs font-semibold text-foreground" htmlFor={`link-job-${conv.id}`}>输入要关联的岗位 ID（可在岗位池详情里查看）</label>
                    <div className="mt-2 flex gap-2">
                      <input
                        id={`link-job-${conv.id}`}
                        value={linkJobId}
                        onChange={event => setLinkJobId(event.target.value)}
                        placeholder="岗位 ID"
                        className="w-64 rounded-lg border border-card-border bg-card px-3 py-1.5 text-sm outline-none focus:border-primary"
                      />
                      <Button size="sm" disabled={!linkJobId.trim() || busyId === conv.id} onClick={link}>确认关联</Button>
                      <Button variant="secondary" size="sm" onClick={() => setLinkTarget(null)}>取消</Button>
                    </div>
                  </div>
                )}

                {draft && (
                  <div className={cn(
                    'mt-3 rounded-xl border p-3',
                    draft.fact_status === 'verified' ? 'border-success/30 bg-success/5' : 'border-warning/30 bg-warning/5'
                  )}>
                    <div className="flex items-center justify-between gap-2">
                      <span className={cn('text-xs font-semibold', draft.fact_status === 'verified' ? 'text-success' : 'text-warning')}>
                        {draft.fact_status === 'verified' ? '草稿已过事实校验' : '草稿未过事实校验（请核对后谨慎使用）'}
                      </span>
                      <Button variant="secondary" size="sm" onClick={() => copyDraft(conv)}>
                        <ClipboardCopy className="mr-2 h-4 w-4" />{copiedId === conv.id ? '已复制' : '复制到剪贴板'}
                      </Button>
                    </div>
                    {draft.draft && <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-foreground">{draft.draft}</p>}
                    {draft.issues?.length > 0 && (
                      <ul className="mt-2 space-y-1 text-xs text-warning">
                        {draft.issues.map((issue, index) => <li key={index}>· {issue}</li>)}
                      </ul>
                    )}
                    {draft.injection_risks?.length > 0 && (
                      <p className="mt-1 text-xs text-warning">HR 消息含可疑指令内容：{draft.injection_risks.join('；')}</p>
                    )}
                  </div>
                )}
              </article>
            )
          })}
        </section>
      )}
    </div>
  )
}
