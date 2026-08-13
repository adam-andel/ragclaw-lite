import { useI18n } from 'vue-i18n'
import cronstrue from 'cronstrue'
// Load Chinese locale data, otherwise toString falls back to 'en' and warns
import 'cronstrue/locales/zh_CN'

/**
 * Convert a 5-field cron expression to human-readable text, following the current i18n locale.
 * - On parse failure, fall back to showing only the raw expression
 * - format() returns "human readable (raw expression)", reusable for list/detail views
 */
export function useCronDescribe() {
  const { locale } = useI18n()

  function describe(expr: string | null | undefined): string {
    if (!expr) return ''
    const cronLocale = locale.value === 'zh-CN' ? 'zh_CN' : 'en'
    try {
      const text = cronstrue.toString(expr, {
        locale: cronLocale,
        throwExceptionOnParseError: true,
      })
      // Fallback: some versions return "Invalid ..." instead of throwing on parse failure
      return /invalid/i.test(text) ? '' : text
    } catch {
      return ''
    }
  }

  function format(expr: string | null | undefined): string {
    if (!expr) return '—'
    const human = describe(expr)
    return human ? `${human} (${expr})` : `(${expr})`
  }

  return { describe, format }
}
