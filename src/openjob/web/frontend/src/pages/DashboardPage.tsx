import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useDashboard, type CollectionProgress, type HistoryItem, type Job, type WorkbenchTask } from '@/hooks/useDashboard'
import { useJobSearch, type JobSortKey, type JobSortOrder } from '@/hooks/useJobSearch'
import { Button } from '@/components/ui/button'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { RecycleBinPanel } from '@/components/dashboard/RecycleBinPanel'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { CollectJobsDialog } from '@/components/dashboard/CollectJobsDialog'
import { ActionItemsCard } from '@/components/dashboard/ActionItemsCard'
import { BestMatchCard } from '@/components/dashboard/BestMatchCard'
const TrendsChart = lazy(() => import('@/components/dashboard/TrendsChart').then(m => ({ default: m.TrendsChart })))
const UsageDonutCard = lazy(() => import('@/components/dashboard/UsageDonutCard').then(m => ({ default: m.UsageDonutCard })))
import { PriorityItemsCard } from '@/components/dashboard/PriorityItemsCard'
import { JobDetailModal } from '@/components/jobs/JobCards'
import { EmptyState } from '@/components/ui/EmptyState'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { parseHistoryDetail } from '@/lib/historyDetail'
import {
  EMPTY_JOB_FILTERS,
  filterJobs,
  hasInvalidSalaryRange,
  useDebouncedValue,
  type JobFilters,
} from '@/lib/jobFilters'
import { getActionLabel, getStatusLabel } from '@/lib/status'
import { cn } from '@/lib/utils'
import {
  AlertTriangle,
  BriefcaseBusiness,
  Download,
  ExternalLink,
  Eye,
  MessageCircle,
  Radar,
  RefreshCw,
  Send,
  Trash2,
  XCircle,
} from 'lucide-react'
import { DashboardHero } from '@/components/dashboard/DashboardHero'
const WeeklyActivityChart = lazy(() => import('@/components/dashboard/WeeklyActivityChart').then(m => ({ default: m.WeeklyActivityChart })))
import { AutomationControlCard } from '@/components/dashboard/AutomationControlCard'
import { PipelineProgress } from '@/components/dashboard/PipelineProgress'

type WorkbenchMode = 'full' | 'collect' | 'rescore' | 'monitor'
type DashboardView = 'workbench' | 'jobs' | 'monitor'
const TASK_STAGE_LABELS = [
  '开始采集岗位',
  '开始 AI 评分',
  '开始重新评分',
  'AI 评分进度',
  '等待前端确认投递',
  '发送失败待处理',
  '执行一轮监测',
  '本轮监测完成，30 分钟后再次检查',
]

function currentTaskStage(logs: string[] = []) {
  for (const log of logs.slice().reverse()) {
    if (log.includes('AI 评分进度')) return log
    if (log.includes('招呼语发送结果')) return log
    if (log.includes('发送招呼语')) return '发送招呼语'
    if (log.includes('生成招呼语')) return '生成招呼语'
    const stage = TASK_STAGE_LABELS.find(label => log.includes(label))
    if (stage) return stage
  }
  return '等待后端返回阶段'
}

function taskStatusText(status: string) {
  if (status === 'failed') return '运行失败'
  if (status === 'completed') return '已结束'
  if (status === 'stopped') return '已停止'
  if (status === 'stopping') return '停止中'
  return '运行中'
}

function taskStatusClass(status: string) {
  if (status === 'failed') return 'border-danger/20 bg-danger/10'
  if (status === 'completed' || status === 'stopped') return 'border-card-border bg-card'
  return 'border-primary/20 bg-accent-soft'
}

function taskStatusTitle(status: string) {
  if (status === 'completed' || status === 'stopped') return '最近任务状态'
  return '当前阶段'
}

function taskStopReasonLabel(reason?: string) {
  if (reason === 'daily_limit') return '今日发送额度已用完，岗位已保留在“待发送招呼语”；明日额度恢复后再重试。'
  if (reason === 'outside_window') return '当前不在发送时间窗口内，岗位已保留在“待发送招呼语”。'
  if (reason === 'day_off') return '今日触发防检测休息策略，岗位已保留在“待发送招呼语”。'
  if (reason === 'stopped') return '任务已按你的要求停止，尚未处理的岗位仍保留在队列中。'
  return reason
}

function taskErrorFeedback(error: string) {
  const normalized = error.toLowerCase()
  if (
    normalized.includes('api key')
    || normalized.includes('authentication')
    || normalized.includes('unauthorized')
    || normalized.includes('401')
    || normalized.includes('403')
  ) {
    return {
      title: 'AI 接口认证失败',
      detail: '请到“配置 → AI 设置”检查 API Key、Base URL 和模型名称，保存后点击“测试连接”。',
    }
  }
  if (
    normalized.includes('chrome')
    || normalized.includes('cdp')
    || normalized.includes('websocket')
    || normalized.includes('browser runtime')
    || normalized.includes('not connected')
  ) {
    return {
      title: 'Google Chrome 连接中断',
      detail: '请确认 Google Chrome 正在运行且已开启远程调试，再点击上方“重新检查”。',
    }
  }
  if (normalized.includes('zhipin') || normalized.includes('登录') || normalized.includes('login')) {
    return {
      title: '招聘平台页面或登录状态异常',
      detail: '请在已连接的 Google Chrome 中打开 BOSS 直聘并确认账号仍处于登录状态。',
    }
  }
  return {
    title: '任务运行失败',
    detail: '请查看原始错误；修复配置或连接问题后，重新运行启动检查。',
  }
}



interface PreflightCheck {
  id: string
  title: string
  status: 'pass' | 'warning' | 'error'
  message: string
  detail: string
  action?: 'config' | 'browser' | ''
}

const modes: Array<{ mode: WorkbenchMode; title: string; description: string }> = [
  {
    mode: 'full',
    title: '运行全流程',
    description: '采集 → AI评分 → 确认投递 → 打招呼 → 持续监测，一次跑完整流程。',
  },
  {
    mode: 'collect',
    title: '单独采集',
    description: '打开岗位采集窗口，选择 BOSS/智联/51job、最大页数、排序和执行顺序；默认只采集不评分。',
  },
  {
    mode: 'monitor',
    title: '单独监测',
    description: '只监测过往已投递项目；发现 HR 要简历或问题后进入对应处理。',
  },
]

const taskMetricItems = [
  { key: 'collect_seen', label: '本轮扫描' },
  { key: 'collect_new', label: '本轮新增' },
  { key: 'collect_duplicate', label: '重复岗位' },
  { key: 'collect_filtered', label: '过滤' },
  { key: 'collect_parse_failed', label: '解析失败' },
  { key: 'collect_save_failed', label: '保存失败' },
  { key: 'ai_passed', label: 'AI通过' },
  { key: 'ai_filtered', label: 'AI过滤' },
  { key: 'ai_failed', label: 'AI失败' },
  { key: 'send_success', label: '发送成功' },
  { key: 'send_deferred', label: '待下次发送' },
  { key: 'send_remaining_quota', label: '今日剩余额度' },
]

export function jobSubtitle(job: Job) {
  return [job.score ? `匹配 ${job.score}` : '', job.salary, job.hr_active || '活跃度未知', getStatusLabel(job.status)].filter(Boolean).join(' · ')
}

async function parsePreflightResponse(res: Response) {
  const rawText = await res.text()
  let data: { ok?: boolean; messages?: unknown; checks?: unknown; error?: string } = {}
  try {
    data = rawText ? JSON.parse(rawText) : {}
  } catch {
    const message = `无法解析预检响应：预检接口返回 ${res.status}`
    return {
      ok: false,
      messages: [message],
      checks: [{ id: 'preflight_api', title: '启动检查', status: 'error', message, detail: '请重启 OpenJob 后重试。' }] as PreflightCheck[],
    }
  }
  const messages = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean) : []
  const checks = Array.isArray(data.checks)
    ? data.checks.filter((item): item is PreflightCheck => Boolean(
      item
      && typeof item === 'object'
      && 'id' in item
      && 'status' in item
      && 'message' in item
    ))
    : []
  if (data.error) messages.push(String(data.error))
  if (!res.ok) messages.push(`预检接口返回 ${res.status}`)
  if (!data.ok && messages.length === 0) messages.push('后端未返回具体原因')
  if (checks.length === 0 && messages.length > 0) {
    checks.push(...messages.map((message, index) => ({
      id: `legacy-${index}`,
      title: '启动检查',
      status: 'error' as const,
      message,
      detail: '请按提示修复后重新检测。',
    })))
  }
  return { ok: Boolean(res.ok && data.ok), messages, checks }
}

function PreflightPanel({
  checks,
  checking,
  onRetry,
}: {
  checks: PreflightCheck[]
  checking: boolean
  onRetry: () => void
}) {
  const actionableChecks = checks.filter(check => check.status !== 'pass')
  if (actionableChecks.length === 0) return null

  const errors = actionableChecks.filter(check => check.status === 'error').length
  const warnings = actionableChecks.filter(check => check.status === 'warning').length
  const needsConfig = actionableChecks.some(check => check.action === 'config')
  const heading = errors ? `启动检查发现 ${errors} 个问题` : `启动检查有 ${warnings} 项提醒`

  return (
    <div className={`mt-3 rounded-3xl border p-4 ${
      errors ? 'border-danger/30 bg-danger/10' : 'border-warning/30 bg-warning/10'
    }`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {errors
            ? <XCircle className="h-5 w-5 text-danger" />
            : <AlertTriangle className="h-5 w-5 text-warning" />}
          <div className="text-sm font-semibold text-foreground">{heading}</div>
        </div>
        <div className="flex items-center gap-2">
          {needsConfig && (
            <Button variant="secondary" size="sm" onClick={() => window.location.assign('/config')}>
              打开配置
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={onRetry} disabled={checking}>
            <RefreshCw className={`mr-2 h-4 w-4 ${checking ? 'animate-spin' : ''}`} />
            {checking ? '检查中' : '重新检查'}
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-2">
        {actionableChecks.map(check => {
          const isError = check.status === 'error'
          return (
            <div
              key={`${check.id}-${check.title}`}
              className={`rounded-2xl border bg-card px-3 py-3 ${isError ? 'border-danger/30' : 'border-warning/30'}`}
            >
              <div className="flex items-start gap-2">
                {isError
                  ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                  : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />}
                <div>
                  <div className="text-xs font-semibold text-muted">{check.title}</div>
                  <div className="mt-0.5 text-sm font-semibold text-foreground">{check.message}</div>
                  <p className="mt-1 text-xs leading-5 text-muted">{check.detail}</p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const {
    workbench,
    history,
    loading,
    error,
    refreshing,
    refresh,
    startTask,
    stopTask,
  } = useDashboard('workbench')
  const [notice, setNotice] = useState('')
  const [preflightChecks, setPreflightChecks] = useState<PreflightCheck[]>([])
  const [preflightMode, setPreflightMode] = useState<WorkbenchMode>('full')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [modePending, setModePending] = useState<WorkbenchMode | null>(null)
  const [collectDialogOpen, setCollectDialogOpen] = useState(false)
  const [collectDialogMode, setCollectDialogMode] = useState<'collect' | 'full'>('collect')

  useEffect(() => {
    const handleConfigSaved = () => { void refresh() }
    window.addEventListener('openjob-config-saved', handleConfigSaved)
    return () => window.removeEventListener('openjob-config-saved', handleConfigSaved)
  }, [refresh])

  const activeTask = workbench.task
  const visibleTask = activeTask || workbench.last_task
  const visibleTaskError = visibleTask?.error ? taskErrorFeedback(visibleTask.error) : null
  const pendingReplies = history.filter(item => item.action === 'reply_pending')

  const runPreflight = async (mode: WorkbenchMode, options?: Record<string, unknown>) => {
    setPreflightMode(mode)
    const res = options
      ? await fetch('/api/workbench/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, options }),
      })
      : await fetch(`/api/workbench/preflight?mode=${mode}`)
    const data = await parsePreflightResponse(res)
    setPreflightChecks(data.checks)
    if (!data.ok) {
      setNotice('请按提示处理后再启动')
      return false
    }
    return true
  }

  const handleModeClick = async (mode: WorkbenchMode) => {
    try {
      if (activeTask?.mode === mode) {
        if (window.confirm(`是否停止当前${activeTask.label}任务？已入库岗位会保留。`)) {
          setModePending(mode)
          setNotice(`正在停止${activeTask.label}...`)
          await stopTask(activeTask.id)
          setNotice(`${activeTask.label}已请求停止。`)
        }
        return
      }
      if (modePending) return
      if (activeTask) {
        setNotice(
          activeTask.status === 'stopping'
            ? `当前${activeTask.label}正在停止，请等待后台完全结束后再启动其他模式。`
            : `当前正在运行${activeTask.label}，请先点击橙色卡片停止后再启动其他模式。`
        )
        return
      }
      if (mode === 'full') {
        setCollectDialogMode('full')
        setCollectDialogOpen(true)
        return
      }
      const target = modes.find(item => item.mode === mode)
      setModePending(mode)
      setNotice(`${target?.title || '任务'}启动前预检中...`)
      if (!(await runPreflight(mode))) return
      setNotice(`${target?.title || '任务'}启动中，请稍候...`)
      await startTask(mode)
      setNotice(`${target?.title || '任务'}已启动，日志会在下方更新。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '操作失败')
    } finally {
      setModePending(null)
    }
  }

  const retryPreflight = async () => {
    if (modePending) return
    try {
      setModePending(preflightMode)
      setNotice('正在重新检查运行环境...')
      const ok = await runPreflight(preflightMode)
      setNotice(ok ? '' : '仍有问题需要处理，请查看检查结果。')
    } catch {
      setNotice('重新检查失败，请确认 OpenJob 后端仍在运行。')
    } finally {
      setModePending(null)
    }
  }

  const startCollection = async (options: Record<string, unknown>) => {
    const mode = collectDialogMode
    setModePending(mode)
    setNotice(mode === 'full' ? '全流程启动前预检中...' : '岗位采集启动前预检中...')
    try {
      if (!(await runPreflight(mode, options))) return
      await startTask(mode, options)
      setCollectDialogOpen(false)
      setNotice(mode === 'full' ? '全流程已启动，进度会在下方更新。' : '岗位采集已启动，进度会在下方更新。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '岗位采集启动失败')
    } finally {
      setModePending(null)
    }
  }

  const openJobDetail = async (job: Job) => {
    try {
      const res = await fetch(`/api/jobs/${job.id}`)
      if (!res.ok) throw new Error('读取岗位详情失败')
      setSelectedJob(await res.json())
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '读取岗位详情失败')
    }
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载中...</div>
  }

  return (
    <div className="space-y-5">
      {/* Bento 顶区：Hero / 近7日行动 / 自动化控制 / 流程进度 */}
      <div className="stagger grid grid-cols-1 gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <div className="xl:col-span-5">
          <DashboardHero
            onRunFullFlow={() => { setCollectDialogMode('full'); setCollectDialogOpen(true) }}
            onOpenCollect={() => { setCollectDialogMode('collect'); setCollectDialogOpen(true) }}
            onRunMonitor={() => { void handleModeClick('monitor') }}
            monitorRunning={activeTask?.mode === 'monitor' && activeTask.status !== 'stopping'}
            refreshing={refreshing}
            onRefresh={() => { void refresh() }}
          />
        </div>
        <div className="xl:col-span-3">
          <Suspense fallback={<div className="h-[212px] rounded-module skeleton" />}>
            <WeeklyActivityChart />
          </Suspense>
        </div>
        <div className="xl:col-span-4">
          <AutomationControlCard
            activeTask={activeTask}
            quota={workbench.send_quota}
            modePending={modePending}
            onRunFullFlow={() => { setCollectDialogMode('full'); setCollectDialogOpen(true) }}
            onStopTask={() => { if (activeTask) void handleModeClick(activeTask.mode as WorkbenchMode) }}
          />
        </div>
        <div className="xl:col-span-8">
          <PipelineProgress funnelToday={workbench.funnel_today} pendingCount={workbench.pending_confirmation.length} />
        </div>
        <div className="xl:col-span-4">
          <section className="flex min-h-[108px] flex-col justify-center rounded-module border border-card-border bg-card px-6 py-5 shadow-card">
            <div className="flex items-center justify-between">
              <h3 className="text-[13px] font-semibold text-muted">任务状态</h3>
              {activeTask && (
                <span className="flex items-center gap-1.5 rounded-full bg-accent-soft px-2.5 py-1 text-[11px] font-semibold text-primary">
                  <span className="breathe h-1.5 w-1.5 rounded-full bg-primary" />
                  {activeTask.label}中
                </span>
              )}
            </div>
            <div className="mt-2 text-[15px] font-semibold text-foreground">
              {activeTask ? currentTaskStage(activeTask.logs) : '空闲 · 等待启动'}
            </div>
            <div className="mt-1 text-xs text-muted">
              {activeTask
                ? `任务状态：${taskStatusText(activeTask.status)}`
                : '启动全流程后，这里会显示实时阶段与进度。'}
            </div>
          </section>
        </div>
      </div>

      {notice && <div className="rise-in rounded-2xl bg-accent-soft px-4 py-3 text-sm text-primary">{notice}</div>}
      {preflightChecks.some(check => check.status !== 'pass') && (
        <PreflightPanel checks={preflightChecks} checking={Boolean(modePending)} onRetry={retryPreflight} />
      )}
      {error && <div className="rise-in rounded-2xl bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div>}
      {activeTask && (
        <div className="rise-in rounded-module border border-card-border bg-card p-4 shadow-card">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold">任务运行状态</div>
              <p className="mt-1 text-xs leading-5 text-muted">如果点击后浏览器没有反应，请先打开 BOSS 直聘并确认已登录；常见失败原因是 BOSS 未登录或 Chrome 调试连接不可用。</p>
            </div>
            <span className="rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-primary">
              {activeTask.label}
            </span>
          </div>
          <div className={`mt-3 rounded-2xl border px-4 py-3 ${taskStatusClass(activeTask.status)}`}>
            <div className="text-xs font-semibold text-primary">{taskStatusTitle(activeTask.status)}</div>
            <div className="mt-1 text-lg font-semibold text-foreground">{currentTaskStage(activeTask.logs)}</div>
            <div className="mt-1 text-xs text-muted">任务状态：{taskStatusText(activeTask.status)}</div>
            {activeTask.deadline_at && (
              <div className="mt-1 text-xs text-muted">
                自动截止：{new Date(activeTask.deadline_at).toLocaleString('zh-CN', { hour12: false })}
              </div>
            )}
            {activeTask.metrics && taskMetricItems.some(item => item.key in activeTask.metrics!) && (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                {taskMetricItems.map(item => (
                  <div key={item.key} className="rounded-xl border border-card-border bg-card px-3 py-2">
                    <div className="text-[10px] text-muted">{item.label}</div>
                    <div className="mt-0.5 text-lg font-semibold text-foreground tabular-nums">{activeTask.metrics?.[item.key] ?? 0}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
          {activeTask.progress?.platforms && <CollectionProgressPanel progress={activeTask.progress} />}
          {activeTask.error && taskErrorFeedback(activeTask.error) && (
            <div className="mt-3 rounded-2xl border border-danger/20 bg-danger/10 px-4 py-3 text-sm text-danger">
              <div className="font-semibold">{taskErrorFeedback(activeTask.error).title}</div>
              <p className="mt-1 text-xs leading-5">{taskErrorFeedback(activeTask.error).detail}</p>
              <details className="mt-2 text-xs text-muted">
                <summary className="cursor-pointer">查看原始错误</summary>
                <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-card p-2">{activeTask.error}</pre>
              </details>
            </div>
          )}
          {activeTask.stop_reason && (
            <div className={`mt-3 rounded-2xl px-3 py-3 text-sm ${activeTask.stop_reason === 'daily_limit' ? 'border border-warning/30 bg-warning/10 text-warning' : 'bg-accent-soft text-primary'}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="font-semibold">{activeTask.stop_reason === 'daily_limit' ? '本次未发送' : '任务说明'}</div>
                  <div className="mt-1">{taskStopReasonLabel(activeTask.stop_reason)}</div>
                </div>
                {activeTask.stop_reason === 'daily_limit' && (
                  <Button size="sm" variant="secondary" onClick={() => { window.location.href = '/config?section=throttle' }}>
                    去设置发送额度
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {workbench.send_quota?.exhausted && (
        <section className="rounded-3xl border border-warning/30 bg-warning/10 p-5 text-warning">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold">今日发送额度已用完</h3>
              <p className="mt-1 text-sm leading-6">
                今日已发送 {workbench.send_quota.sent}/{workbench.send_quota.daily_limit} 条，未发送岗位已保留在“待发送招呼语”；明日额度恢复后再重试。
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={() => { window.location.href = '/config?section=throttle' }}>
              去设置发送额度
            </Button>
          </div>
        </section>
      )}

      {!activeTask && visibleTask && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-card-border bg-card px-4 py-2.5 text-xs text-muted shadow-card">
          <span>上次任务：<span className="font-semibold text-foreground">{visibleTask?.label}</span> · {taskStatusText(visibleTask?.status || '')} · {currentTaskStage(visibleTask?.logs)}</span>
          <button type="button" onClick={() => { window.location.href = '/monitor' }} className="font-semibold text-primary transition-soft hover:opacity-80">查看监测 →</button>
        </div>
      )}

      {/* 第二排 Bento：事项 / 最佳匹配 / 趋势 */}
      <div className="stagger grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12 [&>*]:min-w-0">
        <div className="xl:col-span-4">
          <ActionItemsCard
            pendingCount={workbench.pending_confirmation.length}
            needsResumeCount={workbench.needs_resume.length}
            sendErrorsCount={workbench.send_errors.length}
            pendingRepliesCount={pendingReplies.length}
          />
        </div>
        <div className="xl:col-span-4">
          <BestMatchCard jobs={workbench.pending_confirmation} />
        </div>
        <div className="md:col-span-2 xl:col-span-4">
          <Suspense fallback={<div className="h-[196px] rounded-module skeleton" />}>
            <TrendsChart />
          </Suspense>
        </div>
      </div>

      {/* 第三排：优先事项 / AI 用量 */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-12 [&>*]:min-w-0">
        <div className="xl:col-span-8">
          <PriorityItemsCard
            needsResume={workbench.needs_resume}
            sendErrors={workbench.send_errors}
            topPending={workbench.pending_confirmation}
            onNotice={setNotice}
            onRefresh={() => { void refresh() }}
          />
        </div>
        <div className="xl:col-span-4">
          <Suspense fallback={<div className="h-[196px] rounded-module skeleton" />}>
            <UsageDonutCard />
          </Suspense>
        </div>
      </div>

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
      <CollectJobsDialog
        open={collectDialogOpen}
        mode={collectDialogMode}
        activeTask={activeTask && (activeTask.mode === 'collect' || activeTask.mode === 'full') ? activeTask : null}
        onClose={() => setCollectDialogOpen(false)}
        onStart={options => void startCollection(options)}
      />
    </div>
  )
}

function CollectionProgressPanel({ progress }: { progress: CollectionProgress }) {
  return (
    <div className="mt-3 rounded-2xl border border-primary/20 bg-accent-soft p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-semibold text-primary">多平台采集进度</div>
        <div className="text-xs font-bold text-muted">{progress.outcome === 'running' ? '执行中' : progress.outcome || '已结束'}</div>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {Object.entries(progress.platforms || {}).map(([platform, state]) => (
          <div key={platform} className="rounded-xl border border-card-border bg-card p-3">
            <div className="flex items-center justify-between text-sm font-semibold">
              <span>{platform === 'boss' ? 'BOSS 直聘' : platform === 'zhilian' ? '智联招聘' : '前程无忧'}</span>
              <span>新增 {state.new}</span>
            </div>
            <div className="mt-1 text-xs text-muted">
              {state.status === 'queued' ? '等待前序平台完成' : `${state.city || '城市未开始'} · ${state.keyword || '关键词未开始'} · 第 ${state.page || 0}/${state.max_pages || 0} 页`}
            </div>
            <div className="mt-1 text-xs text-muted">扫描 {state.seen || 0} · 重复 {state.duplicate || 0} · 过滤 {state.filtered || 0} · 解析失败 {state.parse_failed || 0} · 保存失败 {state.save_failed || 0}</div>
            {(state.message || state.reason_code) && <div className="mt-1 text-xs font-bold text-primary">{state.message || state.reason_code}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

