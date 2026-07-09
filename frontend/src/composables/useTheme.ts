import { ref } from 'vue'

/**
 * 浏览器本地独立的明暗主题管理（模块级单例）。
 * - 选择存于 localStorage，按浏览器隔离，用户之间互不影响。
 * - 未显式选择时，跟随系统 prefers-color-scheme。
 * - isDark 为共享 ref：App.vue 用于驱动 NConfigProvider，侧边栏开关用于切换。
 */

const STORAGE_KEY = 'erag-theme'

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
// 用户是否做过显式选择：做过则不再跟随系统
let explicit = stored !== null

const isDark = ref<boolean>(stored ? stored === 'dark' : systemPrefersDark())

function apply() {
  const root = document.documentElement
  if (isDark.value) root.classList.add('dark')
  else root.classList.remove('dark')
}

// 模块加载即应用一次，早于 Vue 挂载，避免首屏闪烁
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

// 仅当用户未显式选择时，实时跟随系统明暗变化
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
