<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { NMessageProvider, NDialogProvider, NConfigProvider, darkTheme, zhCN, enUS, dateZhCN, dateEnUS } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'
import { currentLocale } from '@/i18n/useLocale'
import AppLayout from '@/components/layout/AppLayout.vue'
import LoginView from '@/views/LoginView.vue'
import MessageWrapper from '@/components/MessageWrapper.vue'

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
    // Dark-mode primary button: the original #60a5fa was too light/washed-out, changed to a solid blue #3b82f6 matching light mode,
    // brighten on hover and darken when pressed, keeping the dark-mode primary button clear and not washed-out
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
    // under dark theme baseColor is dark, which would make primary solid-button text black;
    // explicitly lock it to white so the dark-mode primary button text stays readable
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
    <NMessageProvider :duration="10000">
      <MessageWrapper>
        <NDialogProvider>
          <LoginView v-if="!auth.isLoggedIn" />
          <AppLayout v-else />
        </NDialogProvider>
      </MessageWrapper>
    </NMessageProvider>
  </NConfigProvider>
</template>
