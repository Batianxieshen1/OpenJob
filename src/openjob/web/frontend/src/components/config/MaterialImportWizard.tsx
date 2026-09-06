import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Loader2, ShieldCheck, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface SheetMapping {
  source_column: string
  target_field: string
  confidence: number
  reason: string
  sample_values: string[]
}

interface SheetInfo {
  name: string
  hidden: boolean
  header_row: number | null
  columns: string[]
  mapping: SheetMapping[]
  preview: Array<{ row: number; mapped: Record<string, string> }>
  row_count: number
  valid_row_count: number
  inferred_type?: string
  inferred_type_source?: string
  warnings?: string[]
}

const STANDARD_FIELDS: Array<{ value: string; label: string }> = [
  { value: '__ignore__', label: '忽略此列' },
  { value: 'id', label: 'ID' },
  { value: 'type', label: '类型' },
  { value: 'title', label: '标题（必填）' },
  { value: 'organization', label: '组织/机构' },
  { value: 'role', label: '角色/职位' },
  { value: 'start_date', label: '开始时间' },
  { value: 'end_date', label: '结束时间' },
  { value: 'description', label: '描述（必填）' },
  { value: 'achievements', label: '成果' },
  { value: 'skills', label: '技能' },
  { value: 'keywords', label: '关键词' },
  { value: 'target_directions', label: '适用方向' },
  { value: 'source', label: '来源（不进 AI）' },
  { value: 'resume_allowed', label: '可用于简历' },
  { value: 'priority', label: '优先级' },
  { value: 'notes', label: '备注（不进 AI）' },
]

type Phase = 'idle' | 'analyzing' | 'mapping' | 'importing' | 'done' | 'error'

/** 智能导入向导：选择 Excel → 扫描映射 → 预览确认 → 替换正式素材库 */
export function MaterialImportWizard({ onChanged }: { onChanged: () => void }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [submitting, setSubmitting] = useState(false)
  const [importId, setImportId] = useState('')
  const [sourceSha, setSourceSha] = useState('')
  const [filename, setFilename] = useState('')
  const [sheets, setSheets] = useState<SheetInfo[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ count: number; excluded: number } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reset = () => {
    setPhase('idle')
    setImportId('')
    setSheets([])
    setWarnings([])
    setError('')
    setResult(null)
  }

  const handleFile = async (file: File) => {
    setPhase('analyzing')
    setError('')
    setResult(null)
    setFilename(file.name)
    try {
      const body = new FormData()
      body.append('file', file)
      const res = await fetch('/api/resume/materials/analyze', { method: 'POST', body })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '分析失败')
      setImportId(data.import_id)
      setSourceSha(data.source_sha256)
      setSheets(data.sheets || [])
      setWarnings(data.warnings || [])
      setPhase('mapping')
    } catch (err) {
      setError(err instanceof Error ? err.message : '分析失败')
      setPhase('error')
    }
  }

  const updateSheet = (idx: number, patch: Partial<SheetInfo>) => {
    setSheets(prev => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)))
  }

  const updateMapping = (sheetIdx: number, colIdx: number, target: string) => {
    setSheets(prev =>
      prev.map((s, i) => {
        if (i !== sheetIdx) return s
        return {
          ...s,
          mapping: s.mapping.map((m, j) => (j === colIdx ? { ...m, target_field: target } : m)),
        }
      }),
    )
  }

  const confirmImport = async () => {
    setSubmitting(true)
    setError('')
    try {
      const payload = {
        import_id: importId,
        source_sha256: sourceSha,
        sheets: sheets
          .filter(s => !s.hidden && s.header_row !== null)
          .map(s => ({
            name: s.name,
            include: true,
            header_row: s.header_row,
            field_mapping: Object.fromEntries(
              s.mapping.filter(m => m.target_field !== '__ignore__').map(m => [m.source_column, m.target_field]),
            ),
            type_override: s.inferred_type || 'other',
            excluded_rows: [] as number[],
        })),
        defaults: { resume_allowed: true, priority: 3 },
      }
      const res = await fetch('/api/resume/materials/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '导入失败')
      setResult({ count: data.count, excluded: (data.warnings || []).length })
      setPhase('done')
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (phase === 'done') {
    return (
      <div className="rounded-card border border-success/30 bg-success/5 p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-success">
          <CheckCircle2 className="h-4 w-4" />
          导入完成：{result?.count} 条素材已进入正式素材库
        </div>
        {result?.excluded ? (
          <p className="mt-1 text-xs text-muted">{result.excluded} 条记录在导入审计中被排除或忽略。</p>
        ) : null}
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => onChanged()}>查看素材库</Button>
          <Button variant="ghost" size="sm" onClick={reset}>重新导入</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* 选择文件 */}
      <input
        ref={fileRef}
        type="file"
        accept=".xlsx"
        className="hidden"
        onChange={e => {
          const file = e.target.files?.[0]
          if (file) void handleFile(file)
        }}
      />
      <Button size="sm" disabled={phase === 'analyzing'} onClick={() => fileRef.current?.click()}>
        {phase === 'analyzing' ? (
          <><Loader2 className="mr-2 h-4 w-4 animate-spin" />扫描中…</>
        ) : (
          <><Upload className="mr-2 h-4 w-4" />导入自己的 Excel（智能识别）</>
        )}
      </Button>
      <p className="text-[11px] leading-4 text-muted-3">
        系统会先读取表头和数据结构，推测哪些列对应标题、描述、成果等字段；确认前请检查映射。系统不会替你编造缺失经历。
      </p>

      {error && (
        <div className="flex items-start gap-2 rounded-control border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
          <X className="ml-auto h-3.5 w-3.5 cursor-pointer opacity-60" onClick={reset} />
        </div>
      )}

      {/* 映射与预览 */}
      {phase === 'mapping' && (
        <div className="space-y-3">
          {warnings.map(w => (
            <div key={w} className="rounded-control bg-warning/10 px-3 py-1.5 text-[11px] text-warning">{w}</div>
          ))}
          {sheets.map((sheet, sheetIdx) => (
            <div key={sheet.name} className="rounded-card border border-card-border bg-card p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                  <FileSpreadsheet className="h-4 w-4 text-primary" />
                  {sheet.name}
                  <span className="text-xs font-normal text-muted">
                    {sheet.row_count} 行 · 类型推断：{sheet.inferred_type || 'other'}
                  </span>
                </span>
                {sheet.hidden && <span className="rounded-full bg-warning/15 px-2 py-0.5 text-[10px] text-warning">隐藏 Sheet</span>}
              </div>

              {sheet.header_row === null ? (
                <p className="mt-2 text-xs text-warning">无法定位表头行，此 Sheet 将被跳过。</p>
              ) : (
                <>
                  {/* 映射表 */}
                  <table className="mt-2 w-full text-[11px]">
                    <thead>
                      <tr className="text-left text-muted">
                        <th className="px-1 py-1">原始列</th>
                        <th className="px-1 py-1">对应字段</th>
                        <th className="px-1 py-1">样例</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sheet.mapping.map((m, colIdx) => (
                        <tr key={m.source_column + colIdx} className="border-t border-card-border">
                          <td className="max-w-32 truncate px-1 py-1 text-foreground">{m.source_column}</td>
                          <td className="px-1 py-1">
                            <select
                              value={m.target_field}
                              onChange={e => updateMapping(sheetIdx, colIdx, e.target.value)}
                              aria-label={`选择「${m.source_column}」对应的字段`}
                              className={cn(
                                'w-full max-w-36 rounded-control border px-1.5 py-1 outline-none focus:border-primary',
                                m.target_field === '__ignore__' ? 'border-dashed border-card-border text-muted-3' : 'border-card-border text-foreground',
                              )}
                            >
                              {STANDARD_FIELDS.map(f => (
                                <option key={f.value} value={f.value}>{f.label}</option>
                              ))}
                            </select>
                          </td>
                          <td className="max-w-40 truncate px-1 py-1 text-muted-3">
                            {m.sample_values[0] || '—'}
                            {m.confidence < 0.7 && m.target_field !== '__ignore__' && (
                              <span className="ml-1 text-warning">（低置信度）</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* 行预览 */}
                  {sheet.preview.length > 0 && (
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full min-w-[480px] text-[11px]">
                        <thead>
                          <tr className="text-muted">
                            <th className="px-1 py-0.5 text-left font-normal">行</th>
                            {Object.keys(sheet.preview[0]?.mapped || {}).map(k => (
                              <th key={k} className="px-1 py-0.5 text-left font-normal">{k}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {sheet.preview.map(p => (
                            <tr key={p.row} className="border-t border-card-border">
                              <td className="px-1 py-0.5 tabular-nums text-muted-3">{p.row}</td>
                              {Object.entries(p.mapped).map(([k, v]) => (
                                <td key={k} className="max-w-40 truncate px-1 py-0.5 text-foreground">{v}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </div>
          ))}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-[11px] text-muted-3">
              <ShieldCheck className="h-3.5 w-3.5" />
              确认后会替换当前正式素材库；source/notes 不会进入 AI
            </span>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={reset}>取消</Button>
              <Button size="sm" onClick={() => void confirmImport()} disabled={submitting}>
                {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                {submitting ? '导入中…' : '确认导入'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
