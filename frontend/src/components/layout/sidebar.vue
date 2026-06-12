<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, h } from 'vue'
import { NMenu, NIcon, NButton, NTag } from 'naive-ui'
import { Chatbubbles, FolderOpen, Search, StatsChart, LogOut, People } from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menuOptions = computed<MenuOption[]>(() => {
  // Regular users only see chat
  if (!auth.isAdmin) {
    return [
      { label: '对话', key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
    ]
  }
  return [
    { label: '对话', key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
    { label: '知识库', key: '/knowledge', icon: () => h(NIcon, null, { default: () => h(FolderOpen) }) },
    { label: '检索调试', key: '/debug', icon: () => h(NIcon, null, { default: () => h(Search) }) },
    { label: '仪表盘', key: '/dashboard', icon: () => h(NIcon, null, { default: () => h(StatsChart) }) },
    { label: '用户管理', key: '/users', icon: () => h(NIcon, null, { default: () => h(People) }) },
  ]
})

const selectedKey = computed(() => {
  const path = route.path
  if (path.startsWith('/chat')) return '/chat'
  if (path.startsWith('/knowledge')) return '/knowledge'
  return path
})

function handleMenuUpdate(key: string) {
  router.push(key)
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <span class="logo">🔍 ERAG</span>
      <span class="version">Lite</span>
    </div>

    <NMenu
      :value="selectedKey"
      :options="menuOptions"
      :indent="16"
      @update:value="handleMenuUpdate"
    />

    <div class="sidebar-footer">
      <div class="user-info">
        <div class="user-avatar">👤</div>
        <div class="user-detail">
          <div class="user-name">{{ auth.user?.display_name || auth.user?.username }}</div>
          <div class="user-role">
            <NTag size="tiny" :type="auth.isAdmin ? 'error' : 'info'">
              {{ auth.isAdmin ? '管理员' : '用户' }}
            </NTag>
          </div>
        </div>
      </div>
      <NButton text size="tiny" @click="auth.logout">
        <NIcon><LogOut /></NIcon>
        退出
      </NButton>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex; flex-direction: column; flex-shrink: 0;
}
.sidebar-header {
  display: flex; align-items: baseline; gap: 8px;
  padding: 20px 20px 16px; border-bottom: 1px solid var(--color-border);
}
.logo { font-size: 1.25rem; font-weight: 700; color: var(--color-primary); }
.version {
  font-size: 0.7rem; color: var(--color-text-muted);
  background: var(--color-border); padding: 1px 6px; border-radius: 4px;
}
.sidebar-footer {
  margin-top: auto; padding: 12px 16px;
  border-top: 1px solid var(--color-border);
  display: flex; align-items: center; justify-content: space-between;
}
.user-info { display: flex; align-items: center; gap: 8px; }
.user-avatar {
  width: 28px; height: 28px; display: flex; align-items: center; justify-content: center;
  background: var(--color-border); border-radius: 50%; font-size: 0.85rem;
}
.user-name { font-size: 0.82rem; font-weight: 500; }
.user-role { margin-top: 1px; }
</style>
