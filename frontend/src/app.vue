<script setup lang="ts">
import { NMessageProvider } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import AppLayout from '@/components/layout/AppLayout.vue'
import LoginView from '@/views/LoginView.vue'

const auth = useAuthStore()
</script>

<template>
  <NMessageProvider>
    <LoginView v-if="!auth.isLoggedIn" />
    <AppLayout v-else />
  </NMessageProvider>
</template>
