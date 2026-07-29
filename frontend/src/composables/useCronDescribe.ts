import { useI18n } from 'vue-i18n'
import cronstrue from 'cronstrue'

/**
 * 将 5 字段 cron 表达式转为人类可读文本，语言跟随当前 i18n locale。
 * - 解析失败时降级为仅显示原始表达式
 * - format() 返回 "人类可读 (原始表达式)"，列表/详情均可复用
 */
export function useCronDescribe() {
  const { locale } = useI18n()

  function describe(expr: string | null | undefined): string {
    if (!expr) return ''
    const cronLocale = locale.value === 'zh-CN' ? 'zh-cn' : 'en'
    try {
      const text = cronstrue.toString(expr, {
        locale: cronLocale,
        throwExceptionOnParseError: true,
      })
      // 兜底：部分版本在解析失败时返回 "Invalid ..." 而非抛异常
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
