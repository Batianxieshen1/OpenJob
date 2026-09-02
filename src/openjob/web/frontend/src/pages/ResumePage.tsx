import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface JobLite {
  id: string
  title: string
  company: string
  resume_status?: string | null
}

interface DiffBlock {
  section: string
  before: string
  after: string
  reason: string
  risk: string
  adopted: boolean
}

interface ResumeVersion {
  id: string
  version: number
  status: string
  pdf_path: string | null
  risk_flags_json: string | null
  error: string | null
}

interface VersionDetail extends ResumeVersion {
  base_resume_id?: string | null
  jd_analysis_json: string | null
  match_report_json: string | null
  diff_json: string | null
  content_md: string | null
  base_md: string | null
}

type Phase = 'idle' | 'generating' | 'ready' | 'failed'

async function postJson(url: string, body?: unknown) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`)
  return data
}

export default function ResumePage() {
  const [jobs, setJobs] = useState<JobLite[]>([])
  const [jobsLoading, setJobsLoading] = useState(true)
  const [selectedJobId, setSelectedJobId] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [versions, setVersions] = useState<ResumeVersion[]>([])
  const [detail, setDetail] = useState<VersionDetail | null>(null)
  const [diff, setDiff] = useState<DiffBlock[]>([])
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [baseName, setBaseName] = useState('')

  const loadJobs = useCallback(async () => {
    setJobsLoading(true)
    try {
      const requestedJob = new URLSearchParams(window.location.search).get('job')
      const res = await fetch('/api/jobs?limit=100')
      const data = await res.json()
      let list: JobLite[] = data.items || []
      // 有简历版本的岗位永远置顶（不受前 100 限制）
      try {
        const versionsRes = await fetch('/api/resume/jobs-with-versions')
        const versionsData = await versionsRes.json()
        const withVersions: JobLite[] = (versionsData.jobs || []).map((j: { id: string; title: string; company: string }) => ({
          id: j.id,
          title: j.title,
          company: j.company,
        }))
        list = [...withVersions, ...list.filter(j => !withVersions.some(w => w.id === j.id))]
      } catch {}
      // 直达岗位不在列表时，单独拉取并置顶显示
      if (requestedJob && !list.some(j => j.id === requestedJob)) {
        const detailRes = await fetch(`/api/jobs/${requestedJob}`)
        if (detailRes.ok) {
          const detailJob = await detailRes.json()
          list = [detailJob, ...list]
        }
      }
      setJobs(list)
    } catch {
      setJobs([])
    } finally {
      setJobsLoading(false)
    }
  }, [])

  const loadVersions = useCallback(async (jobId: string) => {
    if (!jobId) return
    const res = await fetch(`/api/resume/${jobId}/versions`)
    const data = await res.json()
    setVersions(data.versions || [])
    if ((data.versions || []).length > 0) {
      return data.versions[0].id as string
    }
    setVersions([])
    setDetail(null)
    setDiff([])
    return undefined
  }, [])

  const loadDetail = useCallback(async (resumeId: string) => {
    const res = await fetch(`/api/resume/version/${resumeId}`)
    if (!res.ok) {
      setDetail(null)
      setDiff([])
      return
    }
    const data: VersionDetail = await res.json()
    setDetail(data)
    if (data.base_resume_id) {
      try {
        const basesRes = await fetch('/api/resume/bases')
        const basesData = await basesRes.json()
        const match = (basesData.bases || []).find((b: { id: string }) => b.id === data.base_resume_id)
        setBaseName(match?.name || '')
      } catch {
        setBaseName('')
      }
    } else {
      setBaseName('')
    }
    try {
      setDiff(data.diff_json ? (JSON.parse(data.diff_json) as DiffBlock[]) : [])
    } catch {
      setDiff([])
    }
  }, [])

  useEffect(() => {
    const requestedJob = new URLSearchParams(window.location.search).get('job')
    if (requestedJob) setSelectedJobId(requestedJob)
    loadJobs()
  }, [loadJobs])

  const refreshCurrent = useCallback(
    async (jobId: string) => {
      const latestId = await loadVersions(jobId)
      if (latestId) await loadDetail(latestId)
    },
    [loadVersions, loadDetail],
  )

  useEffect(() => {
    if (selectedJobId) refreshCurrent(selectedJobId)
  }, [selectedJobId, refreshCurrent])

  const generate = async (confirmRegenerate: boolean) => {
    if (!selectedJobId) return
    if (confirmRegenerate && !window.confirm('重新生成将调用 AI 产生新版本，确认继续？')) return
    setPhase('generating')
    setError('')
    setNotice('')
    try {
      const data = await postJson('/api/resume/generate', {
        job_id: selectedJobId,
        confirm_regenerate: confirmRegenerate,
      })
      setNotice(data.reused ? '已有定制简历，直接复用。' : '定制简历已生成，请逐块审阅。')
      await refreshCurrent(selectedJobId)
      setPhase('ready')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPhase('failed')
      await loadVersions(selectedJobId)
    }
  }

  const toggleBlock = (index: number) => {
    setDiff(prev => prev.map((b, i) => (i === index ? { ...b, adopted: !b.adopted } : b)))
  }

  const saveDiff = async () => {
    if (!detail) return
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`/api/resume/version/${detail.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ diff }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '保存失败')
      setNotice('审阅结果已保存，简历已重新合成。')
      await loadDetail(detail.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const exportPdf = async () => {
    if (!detail) return
    setExporting(true)
    setError('')
    try {
      const data = await postJson(`/api/resume/version/${detail.id}/export`, { format: 'docx' })
      setNotice(`PDF 已导出：${data.path}`)
      await loadDetail(detail.id)
      await loadVersions(selectedJobId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
  }

  const markSent = async () => {
    if (!detail) return
    const data = await postJson(`/api/resume/version/${detail.id}/mark-sent`, {
      job_id: selectedJobId,
    }).catch(e => {
      setError(e instanceof Error ? e.message : String(e))
      return null
    })
    if (data) {
      setNotice('已标记为人工发送，投递台账已记录。')
      await loadVersions(selectedJobId)
      await loadDetail(detail.id)
    }
  }

  const jdSummary = useMemo(() => {
    if (!detail?.jd_analysis_json) return null
    try {
      const jd = JSON.parse(detail.jd_analysis_json)
      return {
        title: jd.title as string,
        hard: (jd.hard_requirements || []) as { skill: string; weight: number; required: boolean }[],
        keywords: (jd.keywords || []) as string[],
      }
    } catch {
      return null
    }
  }, [detail])

  const hasDirtyDiff = detail?.diff_json ? JSON.stringify(diff) !== detail.diff_json : false

  return (
    <div className="rise-in mx-auto max-w-[1440px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">简历工作台</h1>
          <p className="text-xs text-muted">
            按 JD 定制简历：逐块审阅修改，确认后导出 PDF；发送永远由你手动完成。
            {baseName && <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-primary">底稿：{baseName}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedJobId}
            onChange={e => setSelectedJobId(e.target.value)}
            className="h-9 min-w-64 rounded-control border border-card-border bg-card px-3 text-sm"
          >
            <option value="">{jobsLoading ? '加载岗位…' : '选择岗位'}</option>
            {jobs.map(j => (
              <option key={j.id} value={j.id}>
                {j.company} · {j.title}
                {j.resume_status ? `（${j.resume_status}）` : ''}
              </option>
            ))}
          </select>
          <Button onClick={() => generate(false)} disabled={!selectedJobId || phase === 'generating'}>
            {phase === 'generating' ? '生成中…' : '按此 JD 优化'}
          </Button>
        </div>
      </header>

      {!selectedJobId && (
        <section className="rise-in rounded-module border border-card-border bg-card p-6 shadow-card">
          <ol className="grid gap-3 sm:grid-cols-3">
            {[
              { step: '1', title: '上传简历底稿', desc: '配置页上传一页式 Word 底稿，支持多方向。' },
              { step: '2', title: '选择目标岗位', desc: '在上方下拉中挑选已评分的岗位。' },
              { step: '3', title: '生成并导出', desc: 'AI 按 JD 定向改写，确认后导出 docx。' },
            ].map(item => (
              <li key={item.step} className="rounded-2xl border border-card-border bg-surface-hover p-4">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink text-xs font-semibold text-shell">{item.step}</span>
                <div className="mt-2 text-sm font-semibold text-foreground">{item.title}</div>
                <p className="mt-1 text-xs leading-5 text-muted">{item.desc}</p>
              </li>
            ))}
          </ol>
          <div className="mt-4 flex justify-end">
            <Button variant="secondary" size="sm" onClick={() => { window.location.href = '/jobs' }}>去岗位池挑选岗位</Button>
          </div>
        </section>
      )}

      {error && (
        <div className="rounded-card border border-danger/40 bg-danger/5 px-4 py-3 text-sm text-danger">{error}</div>
      )}
      {notice && (
        <div className="rounded-card border border-card-border bg-card px-4 py-3 text-sm text-foreground">{notice}</div>
      )}

      {!selectedJobId && !jobsLoading && (
        <div className="rounded-card border border-dashed border-card-border px-6 py-16 text-center">
          <p className="text-sm text-muted">先选择一个岗位，再点击「按此 JD 优化」生成定制简历。</p>
          <p className="mt-1 text-xs text-muted">还没有合适的岗位？先去工作台采集并完成 AI 评分。</p>
        </div>
      )}

      {selectedJobId && phase === 'generating' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-3">
            <div className="skeleton h-8 w-1/3" />
            <div className="skeleton h-24 w-full" />
            <div className="skeleton h-24 w-full" />
          </div>
          <div className="skeleton h-96 w-full" />
        </div>
      )}

      {selectedJobId && phase !== 'generating' && detail && (
        <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)_minmax(0,1fr)]">
          {/* 左：JD 要点 + 版本 */}
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">JD 要点</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-xs">
                {jdSummary ? (
                  <>
                    <div className="font-medium">{jdSummary.title}</div>
                    <ul className="space-y-1 text-muted">
                      {jdSummary.hard.map(h => (
                        <li key={h.skill}>
                          [{h.required ? '硬性' : '加分'}] {h.skill}
                        </li>
                      ))}
                    </ul>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {jdSummary.keywords.map(k => (
                        <span key={k} className="rounded-control bg-accent-soft px-1.5 py-0.5 text-primary">
                          {k}
                        </span>
                      ))}
                    </div>
                  </>
                ) : (
                  <p className="text-muted">暂无 JD 解析结果。</p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">版本</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-xs">
                {versions.length === 0 && (
                  <div className="rounded-xl border border-dashed border-card-border p-3 text-xs text-muted">
                    还没有简历版本。选择岗位后在右侧点「生成定制简历」，第一个版本会出现在这里。
                  </div>
                )}
                {versions.map(v => (
                  <button
                    key={v.id}
                    onClick={() => loadDetail(v.id)}
                    className={`flex w-full items-center justify-between rounded-control px-2 py-1.5 transition-soft hover:bg-surface-hover ${
                      v.id === detail.id ? 'bg-accent-soft text-primary' : ''
                    }`}
                  >
                    <span>v{v.version}</span>
                    <span className="text-muted">{v.status}</span>
                  </button>
                ))}
              </CardContent>
            </Card>
          </div>

          {/* 中：diff 逐块审阅 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold">修改对照（{diff.length} 处）</h2>
              <div className="flex items-center gap-2">
                <Button variant="secondary" onClick={saveDiff} disabled={!hasDirtyDiff || saving}>
                  {saving ? '保存中…' : hasDirtyDiff ? '保存审阅' : '已保存'}
                </Button>
                {versions.find(v => v.id === detail.id)?.status === 'review' && (
                  <Button onClick={exportPdf} disabled={exporting}>
                    {exporting ? '导出中…' : '导出 Word 简历'}
                  </Button>
                )}
                {versions.find(v => v.id === detail.id)?.status === 'exported' && (
                  <Button onClick={markSent}>标记已发送</Button>
                )}
              </div>
            </div>
            {diff.length === 0 && (
              <div className="rounded-card border border-dashed border-card-border px-4 py-10 text-center text-sm text-muted">
                没有待审阅的修改块。
              </div>
            )}
            {diff.map((block, index) => (
              <div
                key={`${block.section}-${index}`}
                className={`rounded-card border p-3 text-xs transition-soft ${
                  block.adopted ? 'border-primary/30 bg-accent-soft' : 'border-card-border bg-card'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{block.section}</span>
                  <label className="flex cursor-pointer items-center gap-1.5 text-muted">
                    <input
                      type="checkbox"
                      checked={block.adopted}
                      onChange={() => toggleBlock(index)}
                      aria-label={`采纳或撤销修改块：${block.section}`}
                      className="accent-primary"
                    />
                    {block.adopted ? '已采纳' : '已撤销'}
                  </label>
                </div>
                <p className="mt-2 rounded-control bg-card p-2 leading-5 text-muted line-through decoration-danger/50">
                  {block.before}
                </p>
                <p className="mt-1 whitespace-pre-wrap leading-5">{block.after}</p>
                <p className="mt-1.5 text-muted">理由：{block.reason}</p>
                {block.risk && (
                  <p className="mt-1 rounded-control bg-warning/10 px-2 py-1 text-warning">待核实：{block.risk}</p>
                )}
              </div>
            ))}
          </div>

          {/* 右：实时预览 */}
          <Card className="self-start">
            <CardHeader className="flex items-center justify-between">
              <CardTitle className="text-sm">实时预览</CardTitle>
              <span className="flex items-center gap-2 text-xs">
                {versions.find(v => v.id === detail.id)?.status === 'exported' && (
                  <>
                    <a href={`/api/resume/version/${detail.id}/download?format=docx`} className="text-primary hover:underline">下载 Word 简历</a>
                  </>
                )}
                <span className="text-muted">
                  {versions.find(v => v.id === detail.id)?.status === 'exported' ? '已导出' : '审阅态'}
                </span>
              </span>
            </CardHeader>
            <CardContent>
              <pre className="max-h-[70vh] overflow-y-auto whitespace-pre-wrap rounded-control bg-surface-hover p-3 text-xs leading-5">
                {detail.content_md || '（无内容）'}
              </pre>
            </CardContent>
          </Card>
        </div>
      )}

      {selectedJobId && phase !== 'generating' && !detail && (
        <div className="rounded-card border border-dashed border-card-border px-6 py-16 text-center">
          <p className="text-sm text-muted">该岗位还没有定制简历。</p>
          <Button className="mt-3" onClick={() => generate(false)}>
            生成第一版
          </Button>
        </div>
      )}

      {/* 幂等复用后允许显式重新生成 */}
      {selectedJobId && detail && (
        <div className="text-center">
          <button
            onClick={() => generate(true)}
            disabled={phase === 'generating'}
            className="text-xs text-muted underline-offset-2 transition-soft hover:text-foreground hover:underline"
          >
            重新生成新版本
          </button>
        </div>
      )}
    </div>
  )
}
