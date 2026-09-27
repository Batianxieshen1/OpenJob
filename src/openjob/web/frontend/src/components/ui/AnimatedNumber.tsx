import { useCountUp } from '@/hooks/useCountUp'

/** 数字从旧值滚动到新值（onetake carry）。全站所有会变化的计数统一用它，
 *  只有 Hero 已单独接 useCountUp。tabular-nums 由调用处的类名控制。 */
export function AnimatedNumber({
  value,
  className,
  duration = 400,
}: {
  value: number
  className?: string
  duration?: number
}) {
  const display = useCountUp(value, duration)
  return <span className={className}>{display}</span>
}
