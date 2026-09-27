import { useLayoutEffect, useRef } from 'react'

/** FLIP 补位：列表项增删后，保留项平滑滑动到新位置（牌桌抽牌效果）。
 *  容器内带 data-flip-id 的元素参与；deps 变化时执行测量与过渡。 */
export function useFlip(deps: unknown[]) {
  const containerRef = useRef<HTMLDivElement>(null)
  const positions = useRef(new Map<string, DOMRect>())

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      container.querySelectorAll<HTMLElement>('[data-flip-id]').forEach(item => {
        positions.current.set(item.dataset.flipId!, item.getBoundingClientRect())
      })
      return
    }
    const items = Array.from(container.querySelectorAll<HTMLElement>('[data-flip-id]'))
    const next = new Map<string, DOMRect>()
    for (const item of items) {
      const id = item.dataset.flipId!
      const rect = item.getBoundingClientRect()
      next.set(id, rect)
      const prev = positions.current.get(id)
      if (!prev) continue
      const dx = prev.left - rect.left
      const dy = prev.top - rect.top
      if (!dx && !dy) continue
      item.style.transition = 'none'
      item.style.transform = `translate(${dx}px, ${dy}px)`
      item.style.willChange = 'transform'
      requestAnimationFrame(() => {
        item.style.transition = 'transform var(--dur-base) var(--ease-product)'
        item.style.transform = ''
        window.setTimeout(() => { item.style.willChange = '' }, 250)
      })
    }
    positions.current = next
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return containerRef
}
