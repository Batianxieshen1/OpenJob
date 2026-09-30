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

/** 流动极光背景（暗黑科技版）：低饱和深色团 + 上浮微光尘埃。
 *  小 canvas 渲染 + CSS 大模糊——深邃而非艳丽，零粒子装饰。 */
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
    // 低饱和深色团：钴蓝/暗青/暗紫，透明度压低——深邃不艳
    const blobs = [
      { rgb: [92, 34, 26], sx: 0.9, sy: 0.55, fx: 0.9, fy: 0.7, r: 0.62 },
      { rgb: [150, 48, 30], sx: 0.5, sy: 0.7, fx: 1.3, fy: 0.5, r: 0.5 },
      { rgb: [104, 40, 34], sx: 0.72, sy: 0.35, fx: 0.7, fy: 1.1, r: 0.55 },
      { rgb: [120, 62, 32], sx: 0.35, sy: 0.3, fx: 1.1, fy: 0.9, r: 0.45 },
    ]
    // 微光尘埃：缓慢上浮的小光点
    const dust = Array.from({ length: 26 }, (_, i) => ({
      x: (i * 137.5) % 120,
      y: (i * 79.3) % 68,
      s: 0.4 + ((i * 37) % 9) / 10,
      v: 0.6 + ((i * 53) % 10) / 12,
      a: 0.18 + ((i * 29) % 10) / 34,
    }))
    let raf = 0
    const t0 = performance.now()
    const render = (now: number) => {
      const t = (now - t0) / 1000
      ctx.globalCompositeOperation = 'source-over'
      ctx.fillStyle = '#171014'
      ctx.fillRect(0, 0, W, H)
      ctx.globalCompositeOperation = 'lighter'
      for (let i = 0; i < blobs.length; i++) {
        const b = blobs[i]
        const x = (b.sx + 0.12 * Math.sin(t * b.fx + i * 2.1)) * W
        const y = (b.sy + 0.14 * Math.cos(t * b.fy + i * 1.7)) * H
        const r = b.r * (0.9 + 0.12 * Math.sin(t * 1.4 + i)) * W
        const g = ctx.createRadialGradient(x, y, 0, x, y, r)
        g.addColorStop(0, `rgba(${b.rgb.join(',')},0.4)`)
        g.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.arc(x, y, r, 0, Math.PI * 2)
        ctx.fill()
      }
      // 尘埃上浮
      for (const d of dust) {
        d.y -= d.v * 0.12
        if (d.y < -2) d.y = H + 2
        ctx.fillStyle = `rgba(238, 178, 150, ${d.a})`
        ctx.fillRect(d.x, d.y, d.s, d.s)
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
      {/* 透视网格地平线：缓缓向前飞行（结构感动效，复古未来风） */}
      <div aria-hidden className="splash-grid pointer-events-none absolute inset-x-0 bottom-0" />
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
        {/* 扫光：字母落定后一道高光横向掠过 */}
        <span aria-hidden className="splash-shine" />
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
