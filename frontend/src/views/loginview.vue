<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NInput, NButton } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value) return
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    router.push('/chat')
  } catch (e: any) {
    console.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <NCard class="login-card">
      <div class="login-header">
        <h1>🔍 ERAG</h1>
        <p>企业级 RAG 知识中台</p>
      </div>

      <form @submit.prevent="handleLogin" class="login-form">
        <NInput v-model:value="username" placeholder="用户名" size="large" />
        <NInput v-model:value="password" type="password" placeholder="密码" size="large" />
        <NButton type="primary" size="large" block :loading="loading" @click="handleLogin">登录</NButton>
      </form>

      <p class="login-hint">请联系管理员创建账号</p>
    </NCard>
  </div>
</template>

<style scoped>
.login-page {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; background: var(--color-bg);
}
.login-card {
  width: 400px; max-width: 90vw;
}
.login-header {
  text-align: center; margin-bottom: 24px;
}
.login-header h1 { font-size: 1.8rem; color: var(--color-primary); margin-bottom: 4px; }
.login-header p { color: var(--color-text-muted); font-size: 0.9rem; }
.login-form {
  display: flex; flex-direction: column; gap: 12px;
}
.login-hint {
  text-align: center; font-size: 0.75rem; color: var(--color-text-muted);
  margin-top: 16px;
}
</style>
