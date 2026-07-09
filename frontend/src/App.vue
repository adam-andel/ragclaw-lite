<script setup lang="ts">
import { onMounted, computed } from 'vue'
import { NMessageProvider, NConfigProvider, darkTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'
import AppLayout from '@/components/layout/AppLayout.vue'
import LoginView from '@/views/LoginView.vue'

const auth = useAuthStore()

const { isDark } = useTheme()
const theme = computed(() => (isDark.value ? darkTheme : undefined))

const lightThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#4338ca',
    primaryColorHover: '#3730a3',
    primaryColorPressed: '#3730a3',
    primaryColorSuppl: '#4338ca',
  },
}
const darkThemeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#6366f1',
    primaryColorHover: '#818cf8',
    primaryColorPressed: '#4f46e5',
    primaryColorSuppl: '#818cf8',
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
  <NConfigProvider :theme="theme" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <LoginView v-if="!auth.isLoggedIn" />
      <AppLayout v-else />
    </NMessageProvider>
  </NConfigProvider>
</template>
