import { ref } from 'vue'
import { i18n } from './index'
import type { AppLocale } from './index'

const STORAGE_KEY = 'ragclaw-locale'

function detectDefaultLocale(): AppLocale {
  if (typeof navigator !== 'undefined') {
    const lang = navigator.language?.toLowerCase() || ''
    if (lang.startsWith('zh')) return 'zh-CN'
  }
  return 'en-US'
}

function readStored(): AppLocale | null {
  if (typeof window === 'undefined') return null
  const v = window.localStorage.getItem(STORAGE_KEY)
  return v === 'zh-CN' || v === 'en-US' ? (v as AppLocale) : null
}

// Module-level singleton — mirrors composables/useTheme.ts so the two
// preference systems (theme + locale) behave identically.
export const currentLocale = ref<AppLocale>(readStored() ?? detectDefaultLocale())

function apply(locale: AppLocale) {
  i18n.global.locale.value = locale
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale
  }
}

// Apply once at module load (before Vue mounts) to avoid a flash of the
// wrong language. Importing this module anywhere triggers it.
apply(currentLocale.value)

export function useLocale() {
  function setLocale(locale: AppLocale) {
    currentLocale.value = locale
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, locale)
    }
    apply(locale)
  }
  return { currentLocale, setLocale }
}
