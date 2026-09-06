import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileSpreadsheet, RefreshCw, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { MaterialImportWizard } from '@/components/config/MaterialImportWizard'

interface MaterialItem {
  id: string
  type: string
  title: string
  organization?: string
  role?: string
  start_date?: string
  end_date?: string
  description: string
  achievements?: string
  skills?: string[]
  keywords?: string[]
  target_directions?: string[]
  priority?: number
}

interface StatusPayload {
  enabled: boolean
  valid: boolean
  stale?: boolean
  source_sha256?: string
  filename: string
  count: number
  sha256: string
  updated_at: string
  errors: string[]
}

const TYPE_LABELS: Record<string, string> = {
  experience: '经历',
  project: '项目',
  award: '奖项',
  student_work: '学生工作',
  campus_activity: '校园活动',
  skill_evidence: '技能佐证',
  certification: '证书',
  other: '其他',
}

/** 配置页素材库面板：上传/替换/预览/模板/刷新/清空（API 读写，不进 React state 存二进制） */
export function ResumeMaterials({ config, updateConfig }: {
  config: { profile?: Record<string, unknown> }
  updateConfig: (path: string, value: unknown) => void
}) {
  const [status, setStatus] = useState<StatusPayload | null>(null)
  const [items, setItems] = useState<MaterialItem[]>([])
  const [typeFilter, setTypeFilter] = useState('')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/resume/materials/status')
      const data: StatusPayload = await res.json()
      setStatus(data)
      return data
    } catch {
      setStatus({ enabled: true, valid: false, filename: '', count: 0, sha256: '', updated_at: '', errors: ['加载失败'] })
      return null
    }
  }, [])

  const loadItems = useCallback(async () => {
    const params = new URLSearchParams()
    if (typeFilter) params.set('type', typeFilter)
    if (query.trim()) params.set('q', query.trim())
    try {
      const res = await fetch('/api/resume/materials?' + params.toString())
      const data = await res.json()
      setItems(Array.isArray(data.items) ? data.items : [])
    } catch {
      setItems([])
    }
  }, [typeFilter, query])

  useEffect(() => {
    void loadStatus().then(s => {
      if (s?.valid) void loadItems()
    })
  }, [loadStatus, loadItems])

  useEffect(() => {
    if (status?.valid) void loadItems()
  }, [typeFilter, query]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleUpload = async (file: File) => {
    setBusy('upload')
    setMessage('')
    try {
      const body = new FormData()
      body.append('file', file)
      const res = await fetch('/api/resume/materials/upload', { method: 'POST', body })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '上传失败')
      setMessage(`已上传：${data.filename}（${data.count} 条素材）`)
      const s = await loadStatus()
      if (s?.valid) await loadItems()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '上传失败')
    } finally {
      setBusy(null)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleRefresh = async () => {
    setBusy('refresh')
    setMessage('')
    try {
      const res = await fetch('/api/resume/materials/refresh', { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '刷新失败')
      setMessage(`索引已刷新：${data.count} 条素材`)
      await loadStatus()
      await loadItems()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '刷新失败')
    } finally {
      setBusy(null)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm('确定清空素材库吗？XLSX 源文件与索引都会删除（简历底稿不受影响）。')) return
    setBusy('delete')
    try {
      const res = await fetch('/api/resume/materials', { method: 'DELETE' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '删除失败')
      setMessage('素材库已清空。')
      setStatus(prev => (prev ? { ...prev, valid: false, count: 0 } : prev))
      setItems([])
    } catch (err) {
      setMessage(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(null)
    }
  }

  const enabled = Boolean(config.profile?.resume_materials_enabled ?? true)

  return (
    <div className="space-y-4">
      <div className="space-y-1 text-xs leading-5 text-muted">
        <p>启用后，生成定制简历时会根据 JD 匹配本地素材并记录引用来源。</p>
        <p>素材库未上传时，系统会继续使用底稿原文，不会阻断生成。</p>
        <p>关闭后，生成流程只使用简历底稿。素材只保存在本机，不会自动发送。</p>
      </div>

      {/* 状态行 */}
      <div className="flex flex-wrap items-center gap-2 rounded-control border border-card-border bg-surface-hover px-3 py-2.5 text-xs">
        <FileSpreadsheet className="h-4 w-4 text-primary" />
        {status?.valid ? (
          <span className="flex flex-wrap items-center gap-2 text-foreground">
            <span
              className={`rounded-full px-2 py-0.5 font-semibold ${status.stale ? 'bg-warning/15 text-warning' : 'bg-success/15 text-success'}`}
            >
              {status.stale ? '索引过期' : '已同步'}
            </span>
            <span className="font-semibold">{status.filename}</span>
            <span className="text-muted tabular-nums">{status.count} 条素材 · {status.updated_at} · {status.sha256}</span>
          </span>
        ) : (
          <span className="text-muted">
            尚未上传素材库{status?.errors?.length ? `（${status.errors[0]}）` : ''}
          </span>
        )}
      </div>

      {status?.stale && (
        <div className="rounded-control border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          检测到 XLSX 源文件已更新，当前预览是旧索引，请点击「刷新索引」。
        </div>
      )}

      {status?.errors?.length ? (
        <div className="rounded-control border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
          {status.errors[0]} —— 修复 XLSX 后可「刷新索引」，或重新上传。
        </div>
      ) : null}

      {/* 智能导入向导：任意 Excel → 分析 → 映射 → 确认 */}
      <MaterialImportWizard onChanged={() => { void loadStatus(); void loadItems() }} />

      {/* 操作行 */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0]
            if (file) void handleUpload(file)
          }}
        />
        <Button
          variant="secondary"
          size="sm"
          disabled={busy !== null}
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="mr-2 h-4 w-4" />
          {busy === 'upload' ? '上传中…' : '上传标准模板'}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy !== null || !status?.filename}
          onClick={() => { window.location.href = '/api/resume/materials/template' }}
        >
          <Download className="mr-2 h-4 w-4" />
          下载模板
        </Button>
        <Button
          variant={status?.stale ? 'default' : 'secondary'}
          size="sm"
          disabled={busy !== null || !status?.filename}
          onClick={() => void handleRefresh()}
        >
          <RefreshCw className={`mr-2 h-4 w-4 ${busy === 'refresh' ? 'animate-spin' : ''}`} />
          刷新索引{status?.stale ? '（源文件已更新）' : ''}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={busy !== null || !status?.valid}
          onClick={() => void handleDelete()}
          className="text-danger hover:border-danger/40"
        >
          <Trash2 className="mr-2 h-4 w-4" />
          清空素材库
        </Button>
      </div>

      {message && (
        <div className={`rounded-control px-3 py-2 text-xs ${message.includes('失败') || message.includes('错误') ? 'bg-danger/10 text-danger' : 'bg-accent-soft text-primary'}`}>
          {message}
        </div>
      )}

      {/* 预览 */}
      {status?.valid && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              aria-label="按类型筛选素材"
              className="h-8 rounded-control border border-card-border bg-card px-2 text-xs"
            >
              <option value="">全部类型</option>
              {Object.entries(TYPE_LABELS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="搜索标题/描述/技能"
              aria-label="搜索素材"
              className="h-8 min-w-40 flex-1 rounded-control border border-card-border bg-card px-2 text-xs outline-none placeholder:text-muted focus:border-primary"
            />
          </div>
          {items.length ? (
            <ul className="max-h-72 space-y-2 overflow-y-auto">
              {items.map(item => (
                <li key={item.id} className="rounded-control border border-card-border bg-card p-3 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-accent-soft px-2 py-0.5 font-semibold text-primary">
                      {TYPE_LABELS[item.type] || item.type}
                    </span>
                    <span className="font-semibold text-foreground">{item.title}</span>
                    <span className="text-muted-3 tabular-nums">优先级 {item.priority ?? 3}</span>
                  </div>
                  {item.description && (
                    <p className="mt-1 line-clamp-2 leading-5 text-muted">{item.description}</p>
                  )}
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-3">
                    {item.organization && <span>{item.organization}</span>}
                    {item.role && <span>{item.role}</span>}
                    {(item.start_date || item.end_date) && <span>{item.start_date || '?'} - {item.end_date || '至今'}</span>}
                    {item.skills?.length ? <span>技能：{item.skills.join('、')}</span> : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-control border border-dashed border-card-border p-3 text-center text-xs text-muted">
              没有匹配的素材。
            </p>
          )}
        </div>
      )}

      {/* 启用开关绑定配置保存 */}
      <label className="flex items-center gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={enabled}
          onChange={e => updateConfig('profile.resume_materials_enabled', e.target.checked)}
          className="h-4 w-4 accent-primary"
        />
        生成定制简历时使用素材库（关闭则只用简历底稿原文）
      </label>
    </div>
  )
}
