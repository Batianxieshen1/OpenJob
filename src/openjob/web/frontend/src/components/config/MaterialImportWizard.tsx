import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, FileSpreadsheet, Loader2, ShieldCheck, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

type MaterialType = 'experience' | 'project' | 'award' | 'student_work' | 'campus_activity' | 'skill_evidence' | 'certification' | 'other'
type RowStatus = 'valid' | 'invalid' | 'example'

interface SheetMapping { source_column: string; target_field: string; confidence: number; reason: string; sample_values: string[] }
interface SheetInfo { name: string; hidden: boolean; header_row: number | null; columns: string[]; mapping: SheetMapping[]; row_count: number; inferred_type?: MaterialType; warnings?: string[] }
interface SheetConfig extends SheetInfo { include: boolean; type_override: MaterialType | null; excluded_rows: number[]; page: number }
interface PreviewRow { excel_row: number; generated_id: string; type: MaterialType; title: string; description: string; status: RowStatus; issues: string[]; excluded: boolean }
interface SheetPreview { columns: string[]; mapping: SheetMapping[]; total_rows: number; valid_rows: number; invalid_rows: number; excluded_rows: number; rows: PreviewRow[]; page: number; total_pages: number }

const STANDARD_FIELDS = [
  ['__ignore__', '忽略此列'], ['id', 'ID'], ['type', '类型'], ['title', '标题（必填）'], ['organization', '组织/机构'], ['role', '角色/职位'], ['start_date', '开始时间'], ['end_date', '结束时间'], ['description', '描述（必填）'], ['achievements', '成果'], ['skills', '技能'], ['keywords', '关键词'], ['target_directions', '适用方向'], ['source', '来源（不进 AI）'], ['resume_allowed', '可用于简历'], ['priority', '优先级'], ['notes', '备注（不进 AI）'],
] as const
const TYPE_OPTIONS: Array<[MaterialType, string]> = [['experience', '工作/实习经历'], ['project', '项目经历'], ['award', '奖项/竞赛'], ['student_work', '学生工作'], ['campus_activity', '校园活动/志愿'], ['skill_evidence', '技能证明'], ['certification', '证书'], ['other', '其他']]
type Phase = 'idle' | 'analyzing' | 'mapping' | 'importing' | 'done' | 'error'

function configPayload(sheet: SheetConfig) {
  return { name: sheet.name, include: sheet.include, header_row: sheet.header_row, field_mapping: Object.fromEntries(sheet.mapping.filter(item => item.target_field !== '__ignore__').map(item => [item.source_column, item.target_field])), type_override: sheet.type_override, excluded_rows: sheet.excluded_rows }
}

/** 导入配置和每一行预览均由服务端暂存的 XLSX 重新计算。 */
export function MaterialImportWizard({ onChanged }: { onChanged: () => void }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [submitting, setSubmitting] = useState(false)
  const [importId, setImportId] = useState('')
  const [sourceSha, setSourceSha] = useState('')
  const [sheets, setSheets] = useState<SheetConfig[]>([])
  const [previews, setPreviews] = useState<Record<string, SheetPreview>>({})
  const [loadingPreview, setLoadingPreview] = useState<Record<string, boolean>>({})
  const [onlyErrors, setOnlyErrors] = useState<Record<string, boolean>>({})
  const [warnings, setWarnings] = useState<string[]>([])
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ count: number; excluded: number; warnings: string[] } | null>(null)
  const [resumeAllowed, setResumeAllowed] = useState(true)
  const [priority, setPriority] = useState(3)
  const fileRef = useRef<HTMLInputElement>(null)
  const requests = useRef<Record<string, AbortController>>({})

  const reset = useCallback(() => {
    Object.values(requests.current).forEach(controller => controller.abort())
    requests.current = {}
    setPhase('idle'); setImportId(''); setSourceSha(''); setSheets([]); setPreviews({}); setLoadingPreview({}); setOnlyErrors({}); setWarnings([]); setError(''); setResult(null); setResumeAllowed(true); setPriority(3)
    if (fileRef.current) fileRef.current.value = ''
  }, [])

  const cancelImport = useCallback(async (): Promise<boolean> => {
    if (!importId) {
      reset()
      return true
    }
    setSubmitting(true); setError('')
    try {
      const response = await fetch(`/api/resume/materials/import/${importId}`, { method: 'DELETE' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || '取消导入失败')
      reset()
      return true
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '取消导入失败，请重试')
      return false
    } finally { setSubmitting(false) }
  }, [importId, reset])

  const fetchPreview = useCallback(async (sheet: SheetConfig, requestedPage = sheet.page, signal?: AbortSignal) => {
    if (!importId || !sourceSha || !sheet.include || !sheet.header_row || sheet.header_row < 1) return
    const response = await fetch('/api/resume/materials/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ import_id: importId, source_sha256: sourceSha, sheet: configPayload(sheet), defaults: { resume_allowed: resumeAllowed, priority }, page: requestedPage, page_size: 50 }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `Sheet「${sheet.name}」预览失败`)
    const preview: SheetPreview = { ...data.sheet, rows: data.rows, page: data.page, total_pages: data.total_pages }
    setPreviews(current => ({ ...current, [sheet.name]: preview }))
    setSheets(current => {
      const existing = current.find(item => item.name === sheet.name)
      if (!existing || existing.mapping.length !== 0 || preview.mapping.length === 0) return current
      return current.map(item => item.name === sheet.name ? { ...item, columns: preview.columns, mapping: preview.mapping } : item)
    })
  }, [importId, priority, resumeAllowed, sourceSha])

  useEffect(() => {
    if (phase !== 'mapping') return
    const timer = window.setTimeout(() => {
      sheets.filter(sheet => sheet.include && sheet.header_row && sheet.header_row > 0).forEach(sheet => {
        requests.current[sheet.name]?.abort()
        const controller = new AbortController(); requests.current[sheet.name] = controller
        setLoadingPreview(current => ({ ...current, [sheet.name]: true }))
        void fetchPreview(sheet, sheet.page, controller.signal).catch(reason => {
          if ((reason as Error).name !== 'AbortError') setError(reason instanceof Error ? reason.message : '预览失败')
        }).finally(() => { if (!controller.signal.aborted) setLoadingPreview(current => ({ ...current, [sheet.name]: false })) })
      })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [fetchPreview, phase, sheets])

  const handleFile = async (file: File) => {
    if (importId && !(await cancelImport())) return
    setPhase('analyzing'); setError(''); setResult(null)
    try {
      const body = new FormData(); body.append('file', file)
      const response = await fetch('/api/resume/materials/analyze', { method: 'POST', body })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || '分析失败')
      setImportId(data.import_id); setSourceSha(data.source_sha256)
      setSheets((data.sheets || []).map((sheet: SheetInfo): SheetConfig => ({ ...sheet, include: !sheet.hidden && sheet.header_row !== null, type_override: null, excluded_rows: [], page: 1 })))
      setWarnings(data.warnings || []); setPhase('mapping')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '分析失败'); setPhase('error') }
  }

  const updateSheet = (name: string, updater: (sheet: SheetConfig) => SheetConfig) => { setError(''); setSheets(current => current.map(sheet => sheet.name === name ? updater(sheet) : sheet)) }
  const setExcluded = (sheetName: string, excelRow: number, excluded: boolean) => updateSheet(sheetName, sheet => ({ ...sheet, excluded_rows: excluded ? [...new Set([...sheet.excluded_rows, excelRow])].sort((a, b) => a - b) : sheet.excluded_rows.filter(row => row !== excelRow) }))
  const excludeCurrentPageErrors = (sheet: SheetConfig) => {
    const preview = previews[sheet.name]; if (!preview) return
    updateSheet(sheet.name, current => ({ ...current, excluded_rows: [...new Set([...current.excluded_rows, ...preview.rows.filter(row => row.status !== 'valid').map(row => row.excel_row)])].sort((a, b) => a - b) }))
  }
  const excludeAllErrors = async (sheet: SheetConfig) => {
    const preview = previews[sheet.name]; if (!preview || preview.invalid_rows === 0) return
    setLoadingPreview(current => ({ ...current, [sheet.name]: true }))
    try {
      const errorRows: number[] = []
      for (let page = 1; page <= preview.total_pages; page += 1) {
        const response = await fetch('/api/resume/materials/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ import_id: importId, source_sha256: sourceSha, sheet: configPayload(sheet), defaults: { resume_allowed: resumeAllowed, priority }, page, page_size: 50 }) })
        const data = await response.json(); if (!response.ok) throw new Error(data.error || '读取错误行失败')
        errorRows.push(...(data.rows as PreviewRow[]).filter(row => row.status !== 'valid').map(row => row.excel_row))
      }
      updateSheet(sheet.name, current => ({ ...current, excluded_rows: [...new Set([...current.excluded_rows, ...errorRows])].sort((a, b) => a - b) }))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '排除错误行失败') } finally { setLoadingPreview(current => ({ ...current, [sheet.name]: false })) }
  }

  const summary = useMemo(() => sheets.filter(sheet => sheet.include).reduce((total, sheet) => {
    const preview = previews[sheet.name]
    if (!preview) return total
    return { valid: total.valid + preview.valid_rows, invalid: total.invalid + preview.invalid_rows, excluded: total.excluded + preview.excluded_rows }
  }, { valid: 0, invalid: 0, excluded: 0 }), [previews, sheets])
  const confirmImport = async () => {
    if (!sheets.some(sheet => sheet.include)) return setError('请至少选择一个 Sheet 导入')
    if (summary.invalid > 0) return setError('仍有未排除的错误行，请修正映射或排除这些行后再确认')
    if (!window.confirm(`确认用 ${summary.valid} 条素材替换当前正式素材库吗？已排除 ${summary.excluded} 条。`)) return
    setSubmitting(true); setPhase('importing'); setError('')
    try {
      const response = await fetch('/api/resume/materials/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ import_id: importId, source_sha256: sourceSha, sheets: sheets.map(configPayload), defaults: { resume_allowed: resumeAllowed, priority } }) })
      const data = await response.json(); if (!response.ok) throw new Error(data.error || '导入失败')
      setResult({ count: data.count, excluded: data.excluded_rows || 0, warnings: data.warnings || [] }); setPhase('done'); setImportId(''); onChanged()
    } catch (reason) { setError(reason instanceof Error ? reason.message : '导入失败'); setPhase('mapping') } finally { setSubmitting(false) }
  }

  if (phase === 'done') return <div className="rounded-card border border-success/30 bg-success/5 p-4"><div className="flex items-center gap-2 text-sm font-semibold text-success"><CheckCircle2 className="h-4 w-4" />导入完成：{result?.count} 条素材已进入正式素材库</div>{result?.excluded ? <p className="mt-1 text-xs text-muted">已按你的选择排除 {result.excluded} 条记录。</p> : null}{result?.warnings.length ? <details className="mt-2 text-xs text-muted"><summary className="cursor-pointer">查看导入提示</summary><ul className="mt-1 space-y-1">{result.warnings.map(item => <li key={item}>{item}</li>)}</ul></details> : null}<div className="mt-3 flex gap-2"><Button variant="secondary" size="sm" onClick={onChanged}>刷新素材库</Button><Button variant="ghost" size="sm" onClick={reset}>重新导入</Button></div></div>

  return <div className="space-y-3"><input ref={fileRef} type="file" accept=".xlsx" className="hidden" onChange={event => { const file = event.target.files?.[0]; if (file) void handleFile(file) }} /><Button size="sm" disabled={phase === 'analyzing' || submitting} onClick={() => fileRef.current?.click()}>{phase === 'analyzing' ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />扫描中…</> : <><Upload className="mr-2 h-4 w-4" />导入自己的 Excel（智能识别）</>}</Button><p className="text-[11px] leading-4 text-muted-3">系统会在本地暂存文件中分析表头和字段；确认前可修正 Sheet、表头、映射、类型和错误行。不会编造缺失经历。</p>{error ? <div className="flex items-start gap-2 rounded-control border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}<button type="button" aria-label="关闭错误提示" className="ml-auto" onClick={() => setError('')}><X className="h-3.5 w-3.5" /></button></div> : null}
    {(phase === 'mapping' || phase === 'importing') ? <div className="space-y-3">{warnings.map(warning => <div key={warning} className="rounded-control bg-warning/10 px-3 py-1.5 text-[11px] text-warning">{warning}</div>)}{sheets.map(sheet => {
      const preview = previews[sheet.name]; const rows = onlyErrors[sheet.name] ? preview?.rows.filter(row => row.status !== 'valid') : preview?.rows; const isLoading = loadingPreview[sheet.name]
      return <section key={sheet.name} className="border border-card-border bg-card p-3"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex min-w-0 items-center gap-2"><FileSpreadsheet className="h-4 w-4 shrink-0 text-primary" /><div className="min-w-0"><h4 className="truncate text-sm font-semibold text-foreground">{sheet.name}</h4><p className="text-[11px] text-muted">{sheet.hidden ? '隐藏 Sheet，可手动包含' : '可见 Sheet'} · 初始类型推断：{sheet.inferred_type || 'other'}</p></div></div><div className="flex items-center gap-2 text-xs text-muted"><span>导入此 Sheet</span><Switch checked={sheet.include} onChange={checked => updateSheet(sheet.name, current => ({ ...current, include: checked }))} /></div></div>
        {sheet.include ? <><div className="mt-3 grid gap-3 md:grid-cols-3"><label className="text-xs text-muted">表头所在行<Input type="number" min={1} value={sheet.header_row ?? ''} onChange={event => updateSheet(sheet.name, current => ({ ...current, header_row: Math.max(1, Number(event.target.value) || 1), mapping: [], page: 1 }))} /></label><label className="text-xs text-muted">类型模式<Select value={sheet.type_override || ''} onChange={event => updateSheet(sheet.name, current => ({ ...current, type_override: (event.target.value || null) as MaterialType | null, page: 1 }))}><option value="">使用每行类别/自动推断</option>{TYPE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></label><div className="self-end text-xs text-muted">{isLoading ? <span className="inline-flex h-9 items-center"><Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />更新预览…</span> : <span className="inline-flex h-9 items-center">{preview ? `${preview.valid_rows} 有效 · ${preview.invalid_rows} 错误 · ${preview.excluded_rows} 已排除` : '等待预览'}</span>}</div></div>
          <div className="mt-3 overflow-x-auto"><table className="min-w-[620px] w-full text-[11px]"><thead><tr className="border-b border-card-border text-left text-muted"><th className="px-1 py-1">原始列</th><th className="px-1 py-1">对应字段</th><th className="px-1 py-1">识别依据</th><th className="px-1 py-1">样例</th></tr></thead><tbody>{sheet.mapping.map((mapping, index) => <tr key={`${mapping.source_column}-${index}`} className="border-b border-card-border/70"><td className="max-w-36 truncate px-1 py-1 text-foreground">{mapping.source_column}</td><td className="px-1 py-1"><Select aria-label={`选择「${mapping.source_column}」对应字段`} value={mapping.target_field} onChange={event => updateSheet(sheet.name, current => ({ ...current, mapping: current.mapping.map((item, itemIndex) => itemIndex === index ? { ...item, target_field: event.target.value } : item), page: 1 }))} className="h-7 max-w-44 px-2 text-[11px]">{STANDARD_FIELDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select></td><td className={cn('px-1 py-1', mapping.confidence < 0.7 && mapping.target_field !== '__ignore__' ? 'text-warning' : 'text-muted')}>{mapping.reason}{mapping.confidence < 0.7 && mapping.target_field !== '__ignore__' ? '（低置信度）' : ''}</td><td className="max-w-48 truncate px-1 py-1 text-muted-3">{mapping.sample_values?.slice(0, 3).join(' / ') || '—'}</td></tr>)}</tbody></table></div>
          {preview ? <div className="mt-3 overflow-x-auto border border-card-border"><div className="flex min-h-9 min-w-[620px] items-center justify-between gap-2 border-b border-card-border bg-surface-hover px-2"><label className="flex items-center gap-1.5 text-[11px] text-muted"><input type="checkbox" checked={Boolean(onlyErrors[sheet.name])} onChange={event => setOnlyErrors(current => ({ ...current, [sheet.name]: event.target.checked }))} />只看错误行</label><div className="flex items-center gap-1"><Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" disabled={!preview.rows.some(row => row.status !== 'valid')} onClick={() => excludeCurrentPageErrors(sheet)}>排除当前页错误</Button><Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" disabled={preview.invalid_rows === 0 || isLoading} onClick={() => void excludeAllErrors(sheet)}>排除全部错误</Button></div></div><table className="min-w-[620px] w-full text-[11px]"><thead><tr className="border-b border-card-border text-left text-muted"><th className="px-2 py-1">排除</th><th className="px-2 py-1">Excel 行</th><th className="px-2 py-1">类型</th><th className="px-2 py-1">标题</th><th className="px-2 py-1">描述摘要</th><th className="px-2 py-1">状态</th></tr></thead><tbody>{rows?.map(row => {
            const rowExcluded = sheet.excluded_rows.includes(row.excel_row)
            return <tr key={row.excel_row} className={cn('border-b border-card-border/70', row.status === 'invalid' && !rowExcluded ? 'bg-danger/5' : rowExcluded ? 'opacity-55' : '')}><td className="px-2 py-1"><input aria-label={`排除第 ${row.excel_row} 行`} type="checkbox" checked={rowExcluded} onChange={event => setExcluded(sheet.name, row.excel_row, event.target.checked)} /></td><td className="px-2 py-1 tabular-nums text-muted">{row.excel_row}</td><td className="px-2 py-1 text-foreground">{row.type}</td><td className="max-w-32 truncate px-2 py-1 text-foreground">{row.title || '—'}</td><td className="max-w-60 truncate px-2 py-1 text-muted">{row.description || '—'}</td><td className={cn('px-2 py-1', row.status === 'invalid' ? 'text-danger' : row.status === 'example' ? 'text-warning' : 'text-success')}>{row.status === 'valid' ? '有效' : row.status === 'example' ? '示例行' : row.issues.join('；')}</td></tr>
          })}{rows?.length === 0 ? <tr><td colSpan={6} className="px-2 py-3 text-center text-muted">当前页没有符合条件的行</td></tr> : null}</tbody></table><div className="flex min-w-[620px] items-center justify-end gap-2 p-2 text-[11px] text-muted"><span>第 {preview.page} / {preview.total_pages} 页</span><button type="button" aria-label="上一页" className="p-1 disabled:opacity-40" disabled={preview.page <= 1 || isLoading} onClick={() => updateSheet(sheet.name, current => ({ ...current, page: current.page - 1 }))}><ChevronLeft className="h-4 w-4" /></button><button type="button" aria-label="下一页" className="p-1 disabled:opacity-40" disabled={preview.page >= preview.total_pages || isLoading} onClick={() => updateSheet(sheet.name, current => ({ ...current, page: current.page + 1 }))}><ChevronRight className="h-4 w-4" /></button></div></div> : null}</> : <p className="mt-2 text-xs text-muted">此 Sheet 不会写入正式素材库。</p>}</section>
    })}<div className="flex flex-wrap items-end justify-between gap-3 border-t border-card-border pt-3"><div className="flex flex-wrap items-end gap-3"><label className="flex items-center gap-2 text-xs text-foreground">默认允许用于简历<Switch checked={resumeAllowed} onChange={setResumeAllowed} /></label><label className="w-28 text-xs text-muted">默认优先级<Select value={String(priority)} onChange={event => setPriority(Number(event.target.value))}>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}</Select></label></div><div className="flex flex-wrap items-center gap-2"><span className="flex items-center gap-1.5 text-[11px] text-muted-3"><ShieldCheck className="h-3.5 w-3.5" />确认后替换正式素材库；source/notes 不会进入 AI</span><Button variant="ghost" size="sm" disabled={submitting} onClick={() => void cancelImport()}>取消</Button><Button size="sm" disabled={submitting || summary.valid === 0} onClick={() => void confirmImport()}>{submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}{submitting ? '导入中…' : '确认导入'}</Button></div></div></div> : null}
  </div>
}
