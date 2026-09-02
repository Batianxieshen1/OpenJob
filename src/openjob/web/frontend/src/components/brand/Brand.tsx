import { useEffect, useState } from 'react'

export const brandAssets = {
  icon: '/brand/openjob-icon.svg',
  logoLight: '/brand/openjob-logo.svg',
  logoDark: '/brand/openjob-logo-dark.svg',
} as const

/** 跟随应用主题（html.dark / html.light 类）而非系统偏好，手动切换即时生效 */
function useIsDarkTheme(): boolean {
  const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'))

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setIsDark(document.documentElement.classList.contains('dark'))
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  return isDark
}

interface BrandMarkProps {
  size?: number
  className?: string
}

/** 方形品牌图标：深色主题外加半透明底避免白色线条融入背景 */
export function BrandMark({ size = 36, className = '' }: BrandMarkProps) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded-2xl bg-surface-hover/80 dark:bg-white/10 ${className}`}
      style={{ width: size + 8, height: size + 8 }}
    >
      <img src={brandAssets.icon} alt="OpenJob" width={size} height={size} className="block object-contain" />
    </span>
  )
}

interface BrandLogoProps {
  maxWidth?: number
  className?: string
}

/** 横向 Logo：按当前主题选择浅色/深色资源 */
export function BrandLogo({ maxWidth = 160, className = '' }: BrandLogoProps) {
  const isDark = useIsDarkTheme()
  return (
    <img
      src={isDark ? brandAssets.logoDark : brandAssets.logoLight}
      alt="OpenJob"
      style={{ maxWidth, height: 'auto' }}
      className={`block object-contain ${className}`}
    />
  )
}
