<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const username = ref('')
const password = ref('')
const loading = ref(false)
const passwordInput = ref<InstanceType<typeof NInput> | null>(null)

async function handleLogin() {
  if (loading.value) return
  // Error prevention: validate before hitting the network
  if (!username.value.trim()) {
    message.warning('请输入用户名')
    return
  }
  if (!password.value) {
    message.warning('请输入密码')
    await nextTick()
    passwordInput.value?.focus()
    return
  }
  loading.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    message.success('登录成功，正在进入…')
    router.push('/chat')
  } catch (e: any) {
    // Visibility of system status: surface the server/network error to the user
    message.error(e?.message || '登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-shell" role="presentation">
      <!-- Brand / value panel (hidden on small screens) -->
      <aside class="brand-panel" aria-hidden="true">
        <div class="brand-blob brand-blob-1" />
        <div class="brand-blob brand-blob-2" />
        <div class="brand-content">
          <div class="brand-logo">🔍 ERAG</div>
          <h2 class="brand-title">企业级 RAG 知识中台</h2>
          <p class="brand-subtitle">把私有知识变成可问答、可溯源的智能资产</p>
          <ul class="brand-features">
            <li><span class="dot" />混合检索：向量语义 + BM25 关键词加权融合</li>
            <li><span class="dot" />私有化部署，数据不出域，全程本地化</li>
            <li><span class="dot" />多角色权限管理，按知识库细粒度授权</li>
          </ul>
        </div>
        <p class="brand-footer">EnterpriseRAG · Lite</p>
      </aside>

      <!-- Form panel -->
      <section class="form-panel">
        <div class="login-header">
          <h1 class="login-title">欢迎回来</h1>
          <p class="login-subtitle">登录以继续使用 ERAG</p>
        </div>

        <form class="login-form" @submit.prevent="handleLogin" novalidate>
          <div class="field">
            <label for="login-username" class="field-label">用户名</label>
            <NInput
              v-model:value="username"
              placeholder="请输入用户名"
              size="large"
              :input-props="{
                id: 'login-username',
                autocomplete: 'username',
                autocapitalize: 'off',
                autocorrect: 'off',
                spellcheck: false,
              }"
            />
          </div>

          <div class="field">
            <label for="login-password" class="field-label">密码</label>
            <NInput
              ref="passwordInput"
              v-model:value="password"
              type="password"
              show-password-on="click"
              placeholder="请输入密码"
              size="large"
              :input-props="{ id: 'login-password', autocomplete: 'current-password' }"
            />
          </div>

          <NButton
            type="primary"
            size="large"
            block
            attr-type="submit"
            :loading="loading"
            :disabled="loading"
          >
            {{ loading ? '登录中…' : '登 录' }}
          </NButton>
        </form>

        <p class="login-hint">
          <span class="hint-icon" aria-hidden="true">🔒</span>
          还没有账号？请联系管理员创建。
        </p>

        <p class="login-copy">© ERAG · 企业级 RAG 知识中台</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(1200px 600px at 100% 0%, rgba(79, 110, 247, 0.08), transparent 60%),
    radial-gradient(900px 500px at 0% 100%, rgba(79, 110, 247, 0.06), transparent 55%),
    var(--color-bg);
}

.login-shell {
  display: flex;
  width: 920px;
  max-width: 100%;
  min-height: 540px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 20px 60px -20px rgba(15, 23, 42, 0.25);
}

/* ===== Brand panel ===== */
.brand-panel {
  position: relative;
  flex: 1.15;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 48px 44px;
  color: #fff;
  background: linear-gradient(150deg, var(--color-primary) 0%, var(--color-primary-hover) 100%);
  overflow: hidden;
}
.brand-blob {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  filter: blur(2px);
}
.brand-blob-1 {
  width: 280px; height: 280px;
  top: -90px; right: -80px;
}
.brand-blob-2 {
  width: 200px; height: 200px;
  bottom: -70px; left: -50px;
  background: rgba(255, 255, 255, 0.08);
}
.brand-content { position: relative; z-index: 1; margin-top: 8px; }
.brand-logo {
  font-size: 1.5rem;
  font-weight: 700;
  letter-spacing: 0.5px;
  margin-bottom: 28px;
}
.brand-title {
  font-size: 1.7rem;
  font-weight: 700;
  line-height: 1.3;
  margin-bottom: 12px;
}
.brand-subtitle {
  font-size: 0.95rem;
  line-height: 1.6;
  opacity: 0.92;
  margin-bottom: 32px;
  max-width: 320px;
}
.brand-features {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.brand-features li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  font-size: 0.9rem;
  line-height: 1.5;
  opacity: 0.96;
}
.brand-features .dot {
  flex-shrink: 0;
  width: 8px; height: 8px;
  margin-top: 7px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.22);
}
.brand-footer {
  position: relative;
  z-index: 1;
  font-size: 0.78rem;
  opacity: 0.8;
  letter-spacing: 0.5px;
}

/* ===== Form panel ===== */
.form-panel {
  flex-shrink: 0;
  width: 420px;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 48px 44px;
}
.login-header { margin-bottom: 28px; }
.login-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 6px;
}
.login-subtitle {
  font-size: 0.9rem;
  color: var(--color-text-muted);
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.field { display: flex; flex-direction: column; gap: 8px; }
.field-label {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--color-text);
}
.login-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 22px;
  font-size: 0.8rem;
  color: var(--color-text-muted);
}
.hint-icon { font-size: 0.85rem; }
.login-copy {
  margin-top: 28px;
  text-align: center;
  font-size: 0.72rem;
  color: var(--color-text-muted);
  opacity: 0.7;
}

/* ===== Responsive ===== */
@media (max-width: 860px) {
  .login-shell {
    width: 100%;
    min-height: auto;
    border-radius: 12px;
  }
  .brand-panel { display: none; }
  .form-panel {
    width: 100%;
    padding: 40px 28px;
  }
}
@media (max-width: 480px) {
  .login-page { padding: 0; }
  .login-shell {
    border-radius: 0;
    border: none;
    box-shadow: none;
    min-height: 100vh;
  }
  .form-panel { padding: 32px 22px; justify-content: center; }
}

/* Respect reduced-motion preferences */
@media (prefers-reduced-motion: reduce) {
  .brand-blob { display: none; }
}
</style>
