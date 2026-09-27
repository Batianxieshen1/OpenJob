import { useEffect, useRef, useState } from 'react'

const SEEN_KEY = 'openjob-splash-seen'

function shouldPlay(): boolean {
  if (typeof window === 'undefined') return false
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return false
  try {
    const today = new Date().toISOString().slice(0, 10)
    if (localStorage.getItem(SEEN_KEY) === today) return false
    localStorage.setItem(SEEN_KEY, today)
    return true
  } catch {
    return false
  }
}

/** 流动极光背景：四个柔和色团在深底上游动（小 canvas 渲染 + CSS 大模糊）。
 *  onetake silk/lightField 的轻量版——"背景没有内容也活着"，零粒子零装饰。 */
function useAuroraCanvas(enabled: boolean) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!enabled) return
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const W = (canvas.width = 120)
    const H = (canvas.height = 68)
    const blobs = [
      { rgb: [23, 48, 122], sx: 0.9, sy: 0.55, fx: 0.9, fy: 0.7, r: 0.62 },
      { rgb: [47, 107, 255], sx: 0.5, sy: 0.7, fx: 1.3, fy: 0.5, r: 0.5 },
      { rgb: [109, 91, 255], sx: 0.72, sy: 0.35, fx: 0.7, fy: 1.1, r: 0.55 },
      { rgb: [30, 109, 140], sx: 0.35, sy: 0.3, fx: 1.1, fy: 0.9, r: 0.45 },
    ]
    let raf = 0
    const t0 = performance.now()
    const render = (now: number) => {
      const t = (now - t0) / 1000
      ctx.globalCompositeOperation = 'source-over'
      ctx.fillStyle = '#0b0e14'
      ctx.fillRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'lighter'
      for (let i = 0; i < blobs.length; i++) {
        const b = blobs[i]
        const x = (b.sx + 0.12 * Math.sin(t * b.fx + i * 2.1)) * W
        const y = (b.sy + 0.14 * Math.cos(t * b.fy + i * 1.7)) * H
        const r = b.r * (0.9 + 0.12 * Math.sin(t * 1.4 + i)) * W
        const g = ctx.createRadialGradient(x, y, 0, x, y, r)
        g.addColorStop(0, `rgba(${b.rgb.join(',')},0.55)`)
        g.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fill()
      }
      raf = requestAnimationFrame(render)
    }
    raf = requestAnimationFrame(render)
    return () => cancelAnimationFrame(raf)
  }, [enabled])

  return ref
}

/** 每日一次的开机画面（用户点名试装）。设计约束：
 *  - 当天只播一次（localStorage 记录），日常刷新零打扰
 *  - 总长 1.75s，点击任意处立即跳过
 *  - 字符逐个升起（letter-rise），整体轻微放大消散——"打开"而非"关闭"
 *  - prefers-reduced-motion 用户完全不播 */
export function SplashScreen({ onDone }: { onDone: () => void }) {
  const [leaving, setLeaving] = useState(false)
  const auroraRef = useAuroraCanvas(true)

  useEffect(() => {
    const t1 = window.setTimeout(() => setLeaving(true), 1250)
    const t2 = window.setTimeout(onDone, 1750)
    const skip = () => {
      setLeaving(true)
      window.setTimeout(onDone, 320)
    }
    window.addEventListener('pointerdown', skip, { once: true })
    return () => {
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.removeEventListener('pointerdown', skip)
    }
  }, [onDone])

  return (
    <div
      role="status"
      aria-label="OpenJob 启动画面"
      className={`splash-screen fixed inset-0 z-[200] flex cursor-pointer flex-col items-center justify-center gap-3 ${leaving ? 'splash-leave' : ''}`}
    >
      {/* 流动极光：色团在深底上游动（CSS 放大 + 大模糊，环境而非装饰） */}
      <canvas ref={auroraRef} aria-hidden className="splash-canvas pointer-events-none absolute inset-0 h-full w-full" />
      {/* 环境光：从底部缓缓升起的蓝晕（ebb 的 lightField 简化版，克制） */}
      <div aria-hidden className="splash-glow pointer-events-none absolute inset-0" />
      <div className="splash-word relative text-[30px] font-extrabold tracking-[0.28em]">
        {'OPENJOB'.split('').map((ch, i) => (
          <span key={i} className="splash-letter inline-block" style={{ animationDelay: `${i * 55}ms` }}>
            {ch}
          </span>
        ))}
        {/* 写入线：从中心向两侧展开（block cursor 写名字的余韵） */}
        <span aria-hidden className="splash-rule" />
      </div>
      <div className="splash-sub text-xs tracking-[0.4em]">求职自动化工作台</div>
      <div className="splash-steps relative flex items-center gap-2.5 text-[11px]">
        {['采集', '评分', '确认', '发送'].map((word, i) => (
          <span key={word} className="splash-step" style={{ animationDelay: `${620 + i * 110}ms` }}>
            {word}
            {i < 3 && <span aria-hidden className="splash-arrow">→</span>}
          </span>
        ))}
      </div>
    </div>
  )
}
