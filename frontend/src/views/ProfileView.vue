<script setup lang="ts">
import { ref, computed, h } from 'vue'
import {
  NForm, NFormItem, NInput, NButton, NAvatar, NIcon,
  NCard, NText, useMessage,
} from 'naive-ui'
import { PersonCircle, ShieldCheckmark, Mail, LockClosed, Create, ImageOutline } from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import client from '@/api/client'
import PageHeader from '@/components/common/PageHeader.vue'

const auth = useAuthStore()
const message = useMessage()

// ── Avatar ──
const avatarEmojis = ['👤', '😎', '🦊', '🐱', '🐶', '🐼', '🐨', '🦁', '🐯', '🐸', '🐙', '🦄', '🐳', '🦋', '🌸', '🔥']
const storedAvatar = localStorage.getItem('erag:avatar')
const selectedAvatar = ref(storedAvatar || '👤')
const showAvatarPicker = ref(false)
const uploading = ref(false)

const MAX_AVATAR_SIZE = 1 * 1024 * 1024 // 1MB
const fileInputRef = ref<HTMLInputElement | null>(null)

const avatarSrc = computed(() => auth.user?.avatar_url || undefined)

function selectAvatar(emoji: string) {
  selectedAvatar.value = emoji
  localStorage.setItem('erag:avatar', emoji)
  showAvatarPicker.value = false
}

function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleAvatarUpload(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  if (!file.type.startsWith('image/')) {
    message.error('请上传图片文件')
    target.value = ''
    return
  }
  if (file.size > MAX_AVATAR_SIZE) {
    message.error(`图片大小不能超过 ${MAX_AVATAR_SIZE / 1024 / 1024}MB`)
    target.value = ''
    return
  }

  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const res = await client.post('/auth/me/avatar', formData)
    auth.user = res.data
    message.success('头像已更新')
    showAvatarPicker.value = false
  } catch (e: any) {
    message.error(e.message || '上传失败')
  } finally {
    uploading.value = false
    target.value = ''
  }
}

async function removeCustomAvatar() {
  uploading.value = true
  try {
    const res = await client.delete('/auth/me/avatar')
    auth.user = res.data
    message.success('已重置为默认头像')
  } catch (e: any) {
    message.error(e.message || '重置失败')
  } finally {
    uploading.value = false
  }
}

// ── Form ──
const form = ref({
  display_name: auth.user?.display_name || '',
  email: auth.user?.email || '',
  password: '',
  passwordConfirm: '',
})

const saving = ref(false)

async function handleSave() {
  if (form.value.password && form.value.password !== form.value.passwordConfirm) {
    message.error('两次输入的密码不一致')
    return
  }
  saving.value = true
  try {
    const payload: Record<string, string> = {
      display_name: form.value.display_name,
      email: form.value.email || '',
    }
    if (form.value.password) {
      payload.password = form.value.password
    }
    const res = await client.put('/auth/me', payload)
    auth.user = res.data
    message.success('个人信息已更新')
    form.value.password = ''
    form.value.passwordConfirm = ''
  } catch (e: any) {
    message.error(e.message || '更新失败')
  } finally {
    saving.value = false
  }
}

// ── Role label ──
const roleLabel = computed(() => {
  switch (auth.user?.role) {
    case 'admin': return '超级管理员'
    case 'moderator': return '普通管理员'
    default: return '普通用户'
  }
})

const roleColor = computed(() => {
  switch (auth.user?.role) {
    case 'admin': return '#ef4444'
    case 'moderator': return '#f59e0b'
    default: return '#3b82f6'
  }
})
</script>

<template>
  <div class="profile-page">
    <PageHeader title="个人信息设置" title-tag="h1" />

    <NCard class="profile-card" :bordered="false">
      <!-- Avatar -->
      <div class="avatar-section">
        <input
          ref="fileInputRef"
          type="file"
          accept="image/*"
          style="display: none"
          @change="handleAvatarUpload"
        />
        <div class="avatar-block" @click="triggerUpload">
          <NAvatar v-if="auth.user?.avatar_url" :size="72" round :src="avatarSrc" :style="{ background: 'transparent' }" />
          <NAvatar v-else :size="72" round :style="{ fontSize: '36px', background: 'var(--color-border)' }">
            {{ selectedAvatar }}
          </NAvatar>
          <div class="avatar-edit-hint">
            <NIcon size="14"><Create /></NIcon>
            <span>更换头像aaa</span>
          </div>
        </div>
        <div v-if="showAvatarPicker" class="avatar-picker">
          <button
            v-for="emoji in avatarEmojis"
            :key="emoji"
            :class="['avatar-emoji-btn', { active: selectedAvatar === emoji && !auth.user?.avatar_url }]"
            @click="selectAvatar(emoji)"
          >{{ emoji }}</button>
          <button class="avatar-upload-btn" title="上传自定义头像" :disabled="uploading" @click="triggerUpload">
            <NIcon size="18"><ImageOutline /></NIcon>
          </button>
          <button v-if="auth.user?.avatar_url" class="avatar-reset-btn" :disabled="uploading" @click="removeCustomAvatar">重置</button>
        </div>
      </div>

      <!-- Form -->
      <NForm label-placement="left" label-width="100" :style="{ maxWidth: '480px' }">
        <NFormItem label="用户名">
          <NInput
            :value="auth.user?.username"
            disabled
            placeholder="用户名不可修改"
          >
            <template #prefix>
              <NIcon :component="PersonCircle" />
            </template>
          </NInput>
        </NFormItem>

        <NFormItem label="角色">
          <div class="role-display" :style="{ color: roleColor }">
            <NIcon :component="ShieldCheckmark" :size="16" />
            <span>{{ roleLabel }}</span>
          </div>
        </NFormItem>

        <NFormItem label="显示名">
          <NInput
            v-model:value="form.display_name"
            placeholder="输入显示名称"
            maxlength="200"
          >
            <template #prefix>
              <NIcon :component="PersonCircle" />
            </template>
          </NInput>
        </NFormItem>

        <NFormItem label="邮箱">
          <NInput
            v-model:value="form.email"
            placeholder="输入邮箱地址"
            :input-props="{ type: 'email' }"
            clearable
          >
            <template #prefix>
              <NIcon :component="Mail" />
            </template>
          </NInput>
        </NFormItem>

        <NFormItem label="新密码">
          <NInput
            v-model:value="form.password"
            type="password"
            placeholder="留空则不修改密码"
            show-password-on="click"
            minlength="4"
          >
            <template #prefix>
              <NIcon :component="LockClosed" />
            </template>
          </NInput>
        </NFormItem>

        <NFormItem v-if="form.password" label="确认密码">
          <NInput
            v-model:value="form.passwordConfirm"
            type="password"
            placeholder="再次输入新密码"
            show-password-on="click"
            minlength="4"
          >
            <template #prefix>
              <NIcon :component="LockClosed" />
            </template>
          </NInput>
        </NFormItem>

        <NFormItem :show-feedback="false">
          <NButton
            type="primary"
            :loading="saving"
            :disabled="!form.display_name.trim()"
            @click="handleSave"
          >
            保存修改
          </NButton>
        </NFormItem>
      </NForm>
    </NCard>
  </div>
</template>

<style scoped>
.profile-page {
  max-width: 680px;
  margin: 0 auto;
}
.profile-card {
  background: var(--color-surface);
  border-radius: var(--radius-xl);
  padding: var(--space-6);
}

/* ── Avatar ── */
.avatar-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: var(--space-8);
  padding-bottom: var(--space-6);
  border-bottom: 1px solid var(--color-border);
}
.avatar-block {
  position: relative;
  cursor: pointer;
  border-radius: 50%;
}
.avatar-edit-hint {
  display: flex;
  align-items: center;
  gap: 4px;
  justify-content: center;
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  transition: color 0.2s;
}
.avatar-block:hover .avatar-edit-hint {
  color: var(--color-primary);
}
.avatar-picker {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: center;
  margin-top: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg);
  border-radius: var(--radius);
}
.avatar-emoji-btn {
  width: 40px;
  height: 40px;
  border: 2px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  font-size: 22px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.avatar-emoji-btn:hover {
  background: var(--color-primary-soft);
  transform: scale(1.15);
}
.avatar-emoji-btn.active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.avatar-upload-btn {
  width: 40px;
  height: 40px;
  border: 2px dashed var(--color-border);
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
  color: var(--color-text-muted);
}
.avatar-upload-btn:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
  background: var(--color-primary-soft);
}
.avatar-reset-btn {
  height: 40px;
  padding: 0 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: transparent;
  cursor: pointer;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  transition: all 0.15s;
  white-space: nowrap;
}
.avatar-reset-btn:hover {
  border-color: #ef4444;
  color: #ef4444;
}

/* ── Role ── */
.role-display {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
  font-size: var(--text-sm);
}
</style>
