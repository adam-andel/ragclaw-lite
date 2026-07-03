<script setup lang="ts">
import { onMounted } from 'vue'
import { NMessageProvider, NConfigProvider } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/layout/AppLayout.vue'
import LoginView from '@/views/LoginView.vue'

const auth = useAuthStore()

onMounted(async () => {
  if (auth.token) await auth.fetchMe()
})
</script>

<template>
  <NConfigProvider>
    <NMessageProvider>
      <LoginView v-if="!auth.isLoggedIn" />
      <AppLayout v-else />
    </NMessageProvider>
  </NConfigProvider>
</template>
