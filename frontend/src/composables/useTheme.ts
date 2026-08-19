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

/**
 * Browser-local, independent light/dark theme management (module-level singleton).
 * - The choice is stored in localStorage, isolated per browser, so users do not affect each other.
 * - When no explicit choice is made, follow the system prefers-color-scheme.
 * - isDark is a shared ref: App.vue uses it to drive NConfigProvider, and the sidebar switch uses it to toggle.
 */

const STORAGE_KEY = 'ragclaw-theme'

type ThemeMode = 'light' | 'dark'

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function readStored(): ThemeMode | null {
  const v = localStorage.getItem(STORAGE_KEY)
  if (v === 'light' || v === 'dark') return v
  return null
}

const stored = readStored()
// Whether the user has made an explicit choice: if so, stop following the system
let explicit = stored !== null

const isDark = ref<boolean>(stored ? stored === 'dark' : systemPrefersDark())

function apply() {
  const root = document.documentElement
  if (isDark.value) root.classList.add('dark')
  else root.classList.remove('dark')
}

// Apply once at module load, before Vue mounts, to avoid first-paint flicker
apply()

function persist() {
  localStorage.setItem(STORAGE_KEY, isDark.value ? 'dark' : 'light')
}

export function setDark(value: boolean) {
  isDark.value = value
  explicit = true
  apply()
  persist()
}

export function toggleTheme() {
  setDark(!isDark.value)
}

// Only follow the system light/dark changes in real time when the user has not made an explicit choice
if (window.matchMedia) {
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = (e: MediaQueryListEvent) => {
    if (explicit) return
    isDark.value = e.matches
    apply()
  }
  mq.addEventListener('change', onChange)
}

export function useTheme() {
  return { isDark, setDark, toggleTheme }
}
