import { useEffect, useState } from 'react'

/** 钤印：放行确认后的朱砂方印「准」。
 *  落印（stamp 曲线敲定）→ 短暂停留 → 消散（离场快于入场）。
 *  单条放行/批量放行共用；size 控制印面（单条 40px、批量 72px）。 */
export function SealStamp({ size = 40, onDone }: { size?: number; onDone: () => void }) {
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    const t1 = window.setTimeout(() => setLeaving(true), 620)
    const t2 = window.setTimeout(onDone, 920)
    return () => {
      window.clearTimeout(t1)
      window.clearTimeout(t2)
    }
  }, [onDone])

  return (
    <div
      role="status"
      aria-label="已放行"
      className={`pointer-events-none fixed inset-0 z-[120] flex items-center justify-center ${leaving ? 'seal-stamp seal-leaving' : 'seal-stamp'}`}
    >
      <div
        className="flex items-center justify-center rounded-lg"
        style={{
          width: size,
          height: size,
          background: 'linear-gradient(150deg, #b3382c, #8f2a20)',
          boxShadow: '0 6px 24px rgba(179,56,44,0.4), inset 0 0 0 2px rgba(255,240,230,0.55), inset 0 0 0 5px rgba(179,56,44,0.9)',
          color: '#fff5ec',
          fontSize: size * 0.52,
          fontWeight: 800,
          fontFamily: '"Songti SC", "SimSun", serif',
          letterSpacing: 0,
        }}
      >
        准
      </div>
    </div>
  )
}
