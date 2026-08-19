// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
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
