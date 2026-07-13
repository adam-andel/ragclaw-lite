<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { NMessageProvider, NConfigProvider, darkTheme, zhCN, enUS, dateZhCN, dateEnUS } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'
import { currentLocale } from '@/i18n/useLocale'
import AppLayout from '@/components/layout/AppLayout.vue'
import LoginView from '@/views/LoginView.vue'

const auth = useAuthStore()

const { isDark } = useTheme()
const theme = computed(() => (isDark.value ? darkTheme : undefined))

// Naive UI built-in component text (pagination, empty states, date pickers…)
// follows the active UI language automatically.
const naiveLocale = computed(() => (currentLocale.value === 'zh-CN' ? zhCN : enUS))
const naiveDateLocale = computed(() => (currentLocale.value === 'zh-CN' ? dateZhCN : dateEnUS))

const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#3b82f6',
    primaryColorHover: '#2563eb',
    primaryColorPressed: '#1d4ed8',
    primaryColorSuppl: '#3b82f6',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
    fontSize: '13px',
    fontSizeMedium: '13px',
    fontSizeSmall: '13px',
  },
}
const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    // 暗色模式主色按钮：原 #60a5fa 偏浅、发白，改为与浅色一致的实心蓝 #3b82f6，
    // hover 提亮、pressed 压深，保证暗色下主按钮清晰不寡淡
    primaryColor: '#3b82f6',
    primaryColorHover: '#60a5fa',
    primaryColorPressed: '#2563eb',
    primaryColorSuppl: '#60a5fa',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
    fontSize: '13px',
    fontSizeMedium: '13px',
    fontSizeSmall: '13px',
  },
  Button: {
    // dark theme 下 baseColor 为深色，会导致 primary 实心按钮文字变黑；
    // 显式锁定为白色，保证深色模式主色按钮文字始终可读
    textColorPrimary: '#ffffff',
    textColorHoverPrimary: '#ffffff',
    textColorPressedPrimary: '#ffffff',
    textColorFocusPrimary: '#ffffff',
    textColorDisabledPrimary: 'rgba(255, 255, 255, 0.5)',
  },
}
const themeOverrides = computed(() =>
  isDark.value ? darkThemeOverrides : lightThemeOverrides,
)

onMounted(async () => {
  if (auth.token) await auth.fetchMe()
})
</script>

<template>
  <NConfigProvider :theme="theme" :theme-overrides="themeOverrides" :locale="naiveLocale" :date-locale="naiveDateLocale">
    <NMessageProvider>
      <LoginView v-if="!auth.isLoggedIn" />
      <AppLayout v-else />
    </NMessageProvider>
  </NConfigProvider>
</template>
