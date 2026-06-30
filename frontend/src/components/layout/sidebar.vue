<script setup lang="ts">
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon, NButton, NTag } from 'naive-ui'
import {
  Chatbubbles, FolderOpen, Search, StatsChart,
  LogOut, People, Settings, ExtensionPuzzle,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const userAvatar = computed(() => localStorage.getItem('erag:avatar') || '👤')

// ── Menu ──

const menuOptions = computed<MenuOption[]>(() => {
  if (!auth.isStaff) {
    return [
      { label: '对话', key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
    ]
  }
  return [
    { label: '对话', key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
    { label: '知识库', key: '/knowledge', icon: () => h(NIcon, null, { default: () => h(FolderOpen) }) },
    { label: '文档管理', key: '/documents', icon: () => h(NIcon, null, { default: () => h(FolderOpen) }) },
    { label: '技能管理', key: '/skills', icon: () => h(NIcon, null, { default: () => h(Settings) }) },
    { label: 'MCP 服务', key: '/mcp', icon: () => h(NIcon, null, { default: () => h(Settings) }) },
    { label: '检索调试', key: '/debug', icon: () => h(NIcon, null, { default: () => h(Search) }) },
    { label: '仪表盘', key: '/dashboard', icon: () => h(NIcon, null, { default: () => h(StatsChart) }) },
    { label: '用户管理', key: '/users', icon: () => h(NIcon, null, { default: () => h(People) }) },
    ...(auth.isAdmin ? [
      { label: '系统设置', key: '/settings', icon: () => h(NIcon, null, { default: () => h(Settings) }) },
      { label: '插件管理', key: '/plugins', icon: () => h(NIcon, null, { default: () => h(ExtensionPuzzle) }) },
    ] : []),
  ]
})

const selectedKey = computed(() => {
  const path = route.path
  if (path.startsWith('/chat')) return '/chat'
  if (path.startsWith('/knowledge')) return '/knowledge'
  if (path.startsWith('/documents')) return '/documents'
  return path
})

function handleMenuUpdate(key: string) {
  if (key === '/chat') {
    router.push('/chat').then(() => {
      window.dispatchEvent(new CustomEvent('erag:reset-chat'))
    })
  } else {
    router.push(key)
  }
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

    <div class="sidebar-spacer" />

    <div class="sidebar-footer">
      <div class="user-info" role="button" tabindex="0" @click="router.push('/profile')" @keydown.enter="router.push('/profile')">
        <div class="user-avatar">{{ userAvatar }}</div>
        <div class="user-detail">
          <div class="user-name">{{ auth.user?.display_name || auth.user?.username }}</div>
          <div class="user-role">
            <NTag size="tiny" :type="auth.isAdmin ? 'error' : auth.isStaff ? 'warning' : 'info'">
              {{ auth.isAdmin ? '超级管理员' : auth.isStaff ? '普通管理员' : '用户' }}
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
  height: 100%;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-5) var(--space-5) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.logo {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--color-primary);
}
.version {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-border);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

/* ── Spacer to push footer to bottom ── */
.sidebar-spacer {
  flex: 1;
}

/* ── Footer ── */
.sidebar-footer {
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.user-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  padding: 2px var(--space-2);
  border-radius: var(--radius);
  transition: background 0.15s;
}
.user-info:hover {
  background: var(--color-primary-soft);
}
.user-avatar {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-border);
  border-radius: 50%;
  font-size: var(--text-sm);
}
.user-name {
  font-size: var(--text-sm);
  font-weight: 500;
}
.user-role {
  margin-top: 1px;
}
</style>
