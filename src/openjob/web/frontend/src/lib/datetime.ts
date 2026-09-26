/** 后端 SQLite CURRENT_TIMESTAMP 是 UTC 裸串（"YYYY-MM-DD HH:MM:SS"）。
 *  统一补 Z 按 UTC 解析，避免各页按本地时区解析造成 +8h 偏差
 *  （2026-09-26 审计 B4：等待天数虚高、7 天过期提前触发、Safari Invalid Date）。 */

export function parseUtc(value?: string | null): Date | null {
  const text = (value || '').trim()
  if (!text) return null
  const looksNaiveUtc = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(text) && !/(?:Z|[+-]\d{2}:?\d{2})$/.test(text)
  const normalized = looksNaiveUtc ? `${text.replace(' ', 'T')}Z` : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatUtcTime(value?: string | null): string {
  const date = parseUtc(value)
  if (!date) return value || ''
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
