import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { STATUS_LABELS } from '@/lib/status'
import { hasActiveJobFilters, type JobFilters } from '@/lib/jobFilters'

interface JobFilterBarProps {
  filters: JobFilters
  onChange: (filters: JobFilters) => void
  onReset: () => void
  invalidSalary?: boolean
  showStatus?: boolean
  showSource?: boolean
  showSourceChannel?: boolean
}

type Option = { value: string; label: string }

/** 筛选下拉 chip：点开从 chip 长出选项面板（W1：pop-in + check-spring + 值变化 tick-pop） */
function ChipSelect({
  label,
  value,
  options,
  onChange,
  ariaLabel,
}: {
  label: string
  value: string
  options: Option[]
  onChange: (value: string) => void
  ariaLabel: string
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const current = options.find(option => option.value === value)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-expanded={open}
        onClick={() => setOpen(prev => !prev)}
        className={`inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-xs transition-soft ${
          value
            ? 'border-primary/40 bg-accent-soft/60 font-semibold text-primary'
            : 'border-card-border bg-card text-muted hover:border-primary/30 hover:text-foreground'
        }`}
      >
        {current ? current.label : label}
        <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-150 ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div className="pop-in pop-origin-top absolute left-0 top-9 z-30 min-w-[168px] rounded-2xl border border-card-border bg-card p-1.5 shadow-pop">
          {options.map(option => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value)
                setOpen(false)
              }}
              className={`flex w-full items-center justify-between gap-3 rounded-xl px-2.5 py-1.5 text-left text-xs transition-soft hover:bg-accent-soft/60 ${
                option.value === value ? 'font-semibold text-primary' : 'text-foreground'
              }`}
            >
              {option.label}
              {option.value === value && <Check className="check-spring h-3.5 w-3.5 shrink-0" aria-hidden />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** 岗位筛选栏（V-a 压缩版）：搜索全宽 + chips 一行 + 低频项折叠进「更多筛选」。
 *  原则：筛选是低频动作，不占首屏；chips 显示当前值，点了才展开选项。 */
export function JobFilterBar({
  filters,
  onChange,
  onReset,
  invalidSalary = false,
  showStatus = false,
  showSource = false,
  showSourceChannel = false,
}: JobFilterBarProps) {
  const update = (key: keyof JobFilters, value: string) => onChange({ ...filters, [key]: value })
  const [moreOpen, setMoreOpen] = useState(
    Boolean(filters.salaryMin || filters.salaryMax || (showSource && filters.sourcePlatform))
  )

  return (
    <div className="mb-3 space-y-2">
      <label className="relative block">
        <Search className="pointer-events-none absolute left-3 top-2 h-4 w-4 text-muted" />
        <Input
          value={filters.query}
          onChange={event => update('query', event.target.value)}
          placeholder="搜索职位、公司、JD 或评分理由"
          className="h-9 pl-9"
          aria-label="关键词"
        />
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <ChipSelect
          label="采集时间"
          ariaLabel="采集时间"
          value={filters.createdWithin}
          onChange={value => update('createdWithin', value)}
          options={[
            { value: '', label: '采集时间：全部' },
            { value: 'today', label: '今天' },
            { value: '3d', label: '近 3 天' },
            { value: '7d', label: '近 7 天' },
          ]}
        />
        <ChipSelect
          label="最低评分"
          ariaLabel="最低评分"
          value={filters.minScore}
          onChange={value => update('minScore', value)}
          options={[
            { value: '', label: '最低评分：不限' },
            { value: '60', label: '60 分以上' },
            { value: '71', label: '71 分以上' },
            { value: '80', label: '80 分以上' },
          ]}
        />
        <ChipSelect
          label="岗位性质"
          ariaLabel="岗位性质"
          value={filters.recruitmentType}
          onChange={value => update('recruitmentType', value)}
          options={[
            { value: '', label: '岗位性质：全部' },
            { value: 'campus', label: '只有实习' },
            { value: 'experienced', label: '只有正式岗' },
          ]}
        />
        <ChipSelect
          label="学历"
          ariaLabel="学历要求"
          value={filters.education}
          onChange={value => update('education', value)}
          options={[
            { value: '', label: '学历：全部' },
            { value: '博士', label: '博士' },
            { value: '硕士', label: '硕士' },
            { value: '本科', label: '本科' },
            { value: '大专', label: '大专' },
            { value: '不限', label: '学历不限' },
            { value: 'unknown', label: '未识别' },
          ]}
        />
        {showStatus && (
          <ChipSelect
            label="状态"
            ariaLabel="岗位状态"
            value={filters.status}
            onChange={value => update('status', value)}
            options={[
              { value: '', label: '全部状态' },
              ...Object.entries(STATUS_LABELS).map(([value, labelText]) => ({ value, label: labelText })),
            ]}
          />
        )}
        <button
          type="button"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(prev => !prev)}
          className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs transition-soft ${
            moreOpen
              ? 'border-primary/40 bg-accent-soft/60 font-semibold text-primary'
              : 'border-card-border bg-card text-muted hover:border-primary/30 hover:text-foreground'
          }`}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
          更多筛选
          <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-150 ${moreOpen ? 'rotate-180' : ''}`} aria-hidden />
        </button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 px-2 text-xs text-muted"
          disabled={!hasActiveJobFilters(filters)}
          onClick={onReset}
        >
          <RotateCcw className="mr-1 h-3 w-3" />重置
        </Button>
      </div>

      {/* 低频筛选：薪资区间 / 来源——高度过渡展开 */}
      <div className="expand-wrap" data-open={moreOpen}>
        <div className="overflow-hidden">
          <div className={`flex flex-wrap items-center gap-2 rounded-2xl border border-card-border bg-surface-hover/60 p-2 ${moreOpen ? 'p-3' : ''}`}>
            <Input
              type="number"
              min="0"
              step="1"
              value={filters.salaryMin}
              onChange={event => update('salaryMin', event.target.value)}
              placeholder="最低薪资 K"
              className="h-8 w-32 text-xs"
              aria-label="最低薪资 K"
            />
            <span className="text-xs text-muted">—</span>
            <Input
              type="number"
              min="0"
              step="1"
              value={filters.salaryMax}
              onChange={event => update('salaryMax', event.target.value)}
              placeholder="最高薪资 K"
              className="h-8 w-32 text-xs"
              aria-label="最高薪资 K"
            />
            {showSourceChannel && (
              <ChipSelect
                label="来源通道"
                ariaLabel="来源通道"
                value={filters.sourceChannel}
                onChange={value => update('sourceChannel', value)}
                options={[
                  { value: '', label: '来源通道：全部' },
                  { value: 'search', label: '搜索流' },
                  { value: 'recommendation', label: '推荐页' },
                ]}
              />
            )}
            {showSource && (
              <ChipSelect
                label="来源平台"
                ariaLabel="来源平台"
                value={filters.sourcePlatform}
                onChange={value => update('sourcePlatform', value)}
                options={[
                  { value: '', label: '来源平台：全部' },
                  { value: 'boss', label: 'BOSS 直聘' },
                  { value: 'zhilian', label: '智联招聘' },
                  { value: '51job', label: '前程无忧' },
                ]}
              />
            )}
          </div>
        </div>
      </div>

      {invalidSalary && <p className="text-xs font-bold text-danger">最低薪资不能高于最高薪资，请调整后再筛选。</p>}
    </div>
  )
}
