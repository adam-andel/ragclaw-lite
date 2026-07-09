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

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#4338ca',
    primaryColorHover: '#3730a3',
    primaryColorPressed: '#3730a3',
    primaryColorSuppl: '#4338ca',
  },
}

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
