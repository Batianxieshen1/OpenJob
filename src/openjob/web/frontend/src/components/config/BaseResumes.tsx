import { useCallback, useEffect, useRef, useState } from 'react'
import { Star, Trash2, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface BaseResume {
  id: string
  name: string
  direction: string
  is_default: number
  chars: number
}

export function BaseResumes() {
  const [bases, setBases] = useState<BaseResume[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [name, setName] = useState('')
  const [direction, setDirection] = useState('')
  const [makeDefault, setMakeDefault] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/resume/bases')
      const data = await res.json()
      setBases(data.bases || [])
    } catch {
      setBases([])
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const upload = async (file: File) => {
    setUploading(true)
    setError('')
    setNotice('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('name', name)
      form.append('direction', direction)
      form.append('is_default', makeDefault ? 'true' : 'false')
      const res = await fetch('/api/resume/bases', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '上传失败')
      setNotice(`已上传底稿「${data.name}」`)
      setName('')
      setDirection('')
      setMakeDefault(false)
      if (fileRef.current) fileRef.current.value = ''
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  const setDefault = async (id: string) => {
    await fetch(`/api/resume/bases/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_default: true }),
    })
    await load()
  }

  const remove = async (id: string) => {
    if (!window.confirm('删除这份底稿？已生成的定制简历不受影响。')) return
    await fetch(`/api/resume/bases/${id}`, { method: 'DELETE' })
    await load()
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs font-bold text-foreground">多方向底稿（可选）</p>
        <p className="mt-0.5 text-xs text-muted">
          上传多份不同方向的简历（如“数据分析”“运营”）。生成定制简历时会自动挑选与岗位 JD 最接近的一份作为底稿；未上传时使用上方默认简历文件。
        </p>
      </div>

      {bases.length > 0 && (
        <ul className="space-y-1.5">
          {bases.map(base => (
            <li
              key={base.id}
              className="flex items-center gap-2 rounded-md border border-card-border bg-card px-3 py-2 text-xs"
            >
              <span className="font-bold text-foreground">{base.name}</span>
              {base.direction && <span className="rounded bg-accent-soft px-1.5 py-0.5 text-primary">{base.direction}</span>}
              {base.is_default === 1 && <span className="rounded bg-success/10 px-1.5 py-0.5 text-success">默认</span>}
              <span className="text-muted">{(base.chars / 1000).toFixed(1)}k 字</span>
              <span className="ml-auto flex items-center gap-1">
                {base.is_default !== 1 && (
                  <button
                    type="button"
                    onClick={() => setDefault(base.id)}
                    aria-label="设为默认"
                    title="设为默认"
                    className="p-1 text-muted transition-soft hover:text-warning"
                  >
                    <Star className="h-3.5 w-3.5" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => remove(base.id)}
                  aria-label="删除底稿"
                  title="删除底稿"
                  className="p-1 text-muted transition-soft hover:text-danger"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-2 rounded-md border border-dashed border-card-border p-3">
        <div className="grid grid-cols-2 gap-2">
          <input
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="底稿名称，如：数据分析方向"
            className="h-8 rounded-md border border-card-border bg-card px-2 text-xs text-foreground outline-none placeholder:text-muted focus:border-primary"
          />
          <input
            value={direction}
            onChange={e => setDirection(e.target.value)}
            placeholder="方向关键词，如：数据分析（选填）"
            className="h-8 rounded-md border border-card-border bg-card px-2 text-xs text-foreground outline-none placeholder:text-muted focus:border-primary"
          />
        </div>
        <div className="flex items-center gap-3">
          <label className={`flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-card-border bg-card px-3 text-xs text-muted transition-soft hover:border-primary/50 hover:text-foreground ${uploading ? 'pointer-events-none opacity-60' : ''}`}>
            <Upload className="h-3.5 w-3.5" />
            {uploading ? '上传中…' : '选择文件上传'}
            <input
              ref={fileRef}
              type="file"
              accept=".md,.docx,.pdf,application/pdf"
              className="hidden"
              onChange={e => {
                const file = e.target.files?.[0]
                if (file) void upload(file)
              }}
            />
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)} className="accent-primary" />
            设为默认
          </label>
        </div>
      </div>

      {error && <p className="rounded-md bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p>}
      {notice && <p className="rounded-md bg-success/10 px-3 py-2 text-xs text-success">{notice}</p>}
      <Button variant="ghost" size="sm" onClick={load}>刷新列表</Button>
    </div>
  )
}
