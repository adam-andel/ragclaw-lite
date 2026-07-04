<script setup lang="ts">
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon, NButton, NTag, NBadge } from 'naive-ui'
import {
  Chatbubbles, FolderOpen, Search, StatsChart,
  LogOut, People, Settings, ExtensionPuzzle, Time,
  Notifications,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useNotificationStore } from '@/stores/notifications'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const notificationStore = useNotificationStore()

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
    { label: '定时任务', key: '/cron-jobs', icon: () => h(NIcon, null, { default: () => h(Time) }) },
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

function goToNotifications() {
  notificationStore.hideToast()
  router.push('/notifications')
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
      <div class="notification-entry" role="button" tabindex="0" @click="goToNotifications" @keydown.enter="goToNotifications">
        <NIcon size="18"><Notifications /></NIcon>
        <span class="notification-label">通知</span>
        <NBadge
          v-if="notificationStore.unreadCount > 0"
          :value="notificationStore.unreadCount"
          :max="99"
          class="notification-badge"
        />
      </div>

      <div class="user-row">
        <div class="user-info-wrapper">
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

          <Transition name="toast-slide">
            <div
              v-if="notificationStore.toastVisible && notificationStore.latestUnread"
              class="notification-toast"
              role="button"
              tabindex="0"
              @click="goToNotifications"
              @keydown.enter="goToNotifications"
            >
              <div class="toast-title">{{ notificationStore.latestUnread.title }}</div>
              <div v-if="notificationStore.latestUnread.content" class="toast-content">
                {{ notificationStore.latestUnread.content }}
              </div>
            </div>
          </Transition>
        </div>
        <NButton text size="tiny" @click="auth.logout">
          <NIcon><LogOut /></NIcon>
          退出
        </NButton>
      </div>
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
  flex-direction: column;
  gap: var(--space-2);
  flex-shrink: 0;
  position: relative;
}
.notification-entry {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  padding: var(--space-2);
  border-radius: var(--radius);
  transition: background 0.15s;
  color: var(--color-text);
}
.notification-entry:hover {
  background: var(--color-primary-soft);
}
.notification-label {
  font-size: var(--text-sm);
  flex: 1;
}
.notification-badge {
  margin-left: auto;
}
.user-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.user-info-wrapper {
  position: relative;
  flex: 1;
  min-width: 0;
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

/* ── Notification toast ── */
.notification-toast {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--color-primary);
  color: #fff;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  cursor: pointer;
  z-index: 10;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.notification-toast:hover {
  filter: brightness(1.1);
}
.toast-title {
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.toast-content {
  font-size: var(--text-xs);
  opacity: 0.9;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Toast slide animation ── */
.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}
.toast-slide-enter-from,
.toast-slide-leave-to {
  transform: translateX(-120%);
  opacity: 0;
}
</style>
