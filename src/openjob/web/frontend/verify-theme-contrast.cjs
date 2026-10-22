// 四组合主题对比度验证：应用主题 × 系统偏好
const puppeteer = require('puppeteer-core')

const OUT = 'C:/Users/暴龙战士wink/Desktop/agent/OpenJob/landing'

function relLum([r, g, b]) {
  const f = v => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
function contrast(a, b) {
  const l1 = relLum(a), l2 = relLum(b)
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
  return (hi + 0.05) / (lo + 0.05)
}
function parseRGB(str) {
  const m = String(str).match(/rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?/)
  if (!m) return null
  return { rgb: [+m[1], +m[2], +m[3]], a: m[4] === undefined ? 1 : +m[4] }
}
// alpha 合成到背景
function composite(fg, bg) {
  const a = fg.a
  return fg.rgb.map((c, i) => Math.round(c * a + bg[i] * (1 - a)))
}

;(async () => {
  const browser = await puppeteer.connect({ browserURL: 'http://127.0.0.1:9222', defaultViewport: null })
  const combos = [
    { app: 'light', sys: 'light', tag: 'L-sysL' },
    { app: 'light', sys: 'dark', tag: 'L-sysD' },   // 用户的 bug 组合
    { app: 'dark', sys: 'dark', tag: 'D-sysD' },
    { app: 'dark', sys: 'light', tag: 'D-sysL' },
  ]
  const results = []
  for (const c of combos) {
    const page = await browser.newPage()
    await page.setCacheEnabled(false)  // 必须：旧 profile 会缓存旧 HTML/CSS，导致验证到旧构建
    await page.setViewport({ width: 1280, height: 900 })
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: c.sys }])
    await page.evaluateOnNewDocument(app => {
      try { localStorage.setItem('openjob-theme', app) } catch (e) {}
    }, c.app)
    await page.goto('http://127.0.0.1:8686/?v=' + Date.now(), { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.evaluate(app => {
      document.documentElement.className = app
    }, c.app)
    await new Promise(r => setTimeout(r, 2000))

    // 注入运行态：把浅色卡换成运行态的 class（真实任务在跑时由 React 渲染，这里模拟）
    const metrics = await page.evaluate(() => {
      const probe = document.createElement('div')
      probe.className = 'bg-ink text-shell dark:bg-accent-soft dark:text-foreground'
      probe.textContent = 'x'
      document.body.appendChild(probe)
      const cs = getComputedStyle(probe)
      const cardBg = cs.backgroundColor
      probe.remove()

      const label = document.createElement('div')
      label.className = 'text-shell/65 dark:text-white/65'
      label.textContent = '今日自动化'
      document.body.appendChild(label)
      const lblColor = getComputedStyle(label).color
      label.remove()

      return { cardBg, lblColor, htmlClass: document.documentElement.className }
    })
    const bg = parseRGB(metrics.cardBg)
    const fg = parseRGB(metrics.lblColor)
    const ratio = fg && bg ? contrast(composite(fg, bg.rgb), bg.rgb) : null
    results.push({ ...c, htmlClass: metrics.htmlClass, cardBg: metrics.cardBg, lblColor: metrics.lblColor, ratio: ratio ? +ratio.toFixed(2) : null })

    await page.screenshot({ path: `${OUT}/theme-${c.tag}.png` })
    await page.close()
  }
  console.log(JSON.stringify(results, null, 2))
  const lightPair = results.filter(r => r.app === 'light')
  const darkPair = results.filter(r => r.app === 'dark')
  const sameBg = (a, b) => a.cardBg === b.cardBg
  console.log('\n=== 判定 ===')
  console.log('浅色两组合卡片底一致:', sameBg(lightPair[0], lightPair[1]), '→', lightPair[0].cardBg, 'vs', lightPair[1].cardBg)
  console.log('深色两组合卡片底一致:', sameBg(darkPair[0], darkPair[1]), '→', darkPair[0].cardBg, 'vs', darkPair[1].cardBg)
  for (const r of results) {
    console.log(`  ${r.tag}: 底 ${r.cardBg} | 字 ${r.lblColor} | 对比度 ${r.ratio} ${r.ratio >= 4.5 ? '✅' : r.ratio >= 3 ? '⚠️' : '❌'}`)
  }
  await browser.disconnect()
})().catch(e => { console.error('FAIL:', e.message.slice(0, 200)); process.exit(1) })
