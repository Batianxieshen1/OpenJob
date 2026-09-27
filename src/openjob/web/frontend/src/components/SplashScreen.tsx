import { useEffect, useState } from 'react'

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

/** 每日一次的开机画面（用户点名试装）。设计约束：
 *  - 当天只播一次（localStorage 记录），日常刷新零打扰
 *  - 总长 1.75s，点击任意处立即跳过
 *  - 字符逐个升起（letter-rise），整体轻微放大消散——"打开"而非"关闭"
 *  - prefers-reduced-motion 用户完全不播 */
export function SplashScreen({ onDone }: { onDone: () => void }) {
  const [leaving, setLeaving] = useState(false)

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
