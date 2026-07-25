import { currentLocale } from './useLocale'
import { parseUtcTs } from '@/utils/datetime'

/**
 * Locale-aware date/time helpers. Use these instead of
 * `toLocaleString('zh-CN')` so formatting follows the active UI language.
 */
export function formatDateTime(value: string | number | Date): string {
  const d = typeof value === 'string' ? parseUtcTs(value) : value instanceof Date ? value : new Date(value)
  if (!d || isNaN(d.getTime())) return '-'
  return new Intl.DateTimeFormat(currentLocale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}

export function formatDate(value: string | number | Date): string {
  const d = typeof value === 'string' ? parseUtcTs(value) : value instanceof Date ? value : new Date(value)
  if (!d || isNaN(d.getTime())) return '-'
  return new Intl.DateTimeFormat(currentLocale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d)
}
