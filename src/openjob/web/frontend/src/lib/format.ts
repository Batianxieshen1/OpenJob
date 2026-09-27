/** 评分理由的人话摘要：卡片上不显示五维原始分（那是给 AI/详情弹窗看的）。
 *  优先展示"缺失: …"（最有行动价值），否则取分号后的人话总结段。 */
export function humanizeScoreReason(reason?: string | null): string {
  const text = (reason || '').trim()
  if (!text) return ''
  const withoutPrefix = text.replace(/^\[重评\]\s*/, '')
  const missingMatch = withoutPrefix.match(/(?:缺失|缺)[:：]\s*(.+)$/)
  if (missingMatch) return `缺：${missingMatch[1].trim()}`
  const parts = withoutPrefix.split(/[；;]/).filter(Boolean)
  const human = parts.find(part => !/\d+\/\d+/.test(part))
  return (human || parts[parts.length - 1] || withoutPrefix).trim()
}
