<script setup lang="ts">
import { computed, h } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon, NButton, NTag, NBadge, NSwitch, NSelect } from 'naive-ui'
import {
  Chatbubbles, DocumentText, StatsChart, Bulb, Flash,
  LogOut, People, Settings, Time, FolderOpen,
  Notifications,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useNotificationStore } from '@/stores/notifications'
import { useChatUnreadStore } from '@/stores/chatUnread'
import { useTheme } from '@/composables/useTheme'
import { useLocale } from '@/i18n/useLocale'
import { SUPPORTED_LOCALES } from '@/i18n'
import type { AppLocale } from '@/i18n'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const notificationStore = useNotificationStore()
const { isDark, setDark } = useTheme()
const { t } = useI18n()
const { currentLocale, setLocale } = useLocale()
const chatUnread = useChatUnreadStore()

// Chat menu label with an optional red dot indicating an answer finished
// streaming while the user was on another page.
function chatLabel() {
  return h('span', { class: 'menu-label-with-dot' }, [
    h('span', t('nav.chat')),
    chatUnread.hasUnread ? h('span', { class: 'menu-unread-dot' }) : null,
  ])
}

const userAvatar = computed(() => auth.user?.avatar_url || '')
const userEmoji = computed(() => localStorage.getItem('ragclaw:avatar') || '👤')

// ── Menu ──

const menuOptions = computed<MenuOption[]>(() => {
  const workspaceItem = {
    label: t('nav.workspace'), key: '/workspace',
    icon: () => h(NIcon, null, { default: () => h(FolderOpen) }),
  }
  if (!auth.isStaff) {
    return [
      { label: () => chatLabel(), key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
      workspaceItem,
      { label: t('nav.documents'), key: '/documents', icon: () => h(NIcon, null, { default: () => h(DocumentText) }) },
      { label: t('nav.cron'), key: '/cron-jobs', icon: () => h(NIcon, null, { default: () => h(Time) }) },
    ]
  }
  return [
    { label: () => chatLabel(), key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
    workspaceItem,
    { label: t('nav.documents'), key: '/documents', icon: () => h(NIcon, null, { default: () => h(DocumentText) }) },
    { label: t('nav.cron'), key: '/cron-jobs', icon: () => h(NIcon, null, { default: () => h(Time) }) },
    ...(auth.isAdmin ? [
      { label: t('nav.skills'), key: '/skills', icon: () => h(NIcon, null, { default: () => h(Bulb) }) },
      { label: t('nav.mcp'), key: '/mcp', icon: () => h(NIcon, null, { default: () => h(Flash) }) },
    ] : []),
    { label: t('nav.users'), key: '/users', icon: () => h(NIcon, null, { default: () => h(People) }) },
    ...(auth.isAdmin ? [
      { label: t('nav.settings'), key: '/settings', icon: () => h(NIcon, null, { default: () => h(Settings) }) },
    ] : []),
  ]
})

const selectedKey = computed(() => {
  const path = route.path
  if (path.startsWith('/chat')) return '/chat'
  if (path.startsWith('/documents')) return '/documents'
  if (path.startsWith('/workspace')) return '/workspace'
  return path
})

function handleMenuUpdate(key: string) {
  if (key === '/chat') {
    // Returning to the chat page must NOT reset its state. Restore the right
    // conversation with the following priority:
    //   1. An answer that finished streaming while the user was away (unread) —
    //      opening it also clears the sidebar red dot.
    //   2. Otherwise the last opened conversation, so leaving/entering the chat
    //      page preserves the previously viewed conversation.
    let target = '/chat'
    if (chatUnread.hasUnread && chatUnread.lastConversationId) {
      target = `/chat/${chatUnread.lastConversationId}`
    } else {
      const last = localStorage.getItem('ragclaw:last-conv')
      if (last) target = `/chat/${last}`
    }
    router.push(target)
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
      <span class="logo">RAGClaw</span>
      <span class="version">Lite</span>
    </div>

    <div class="sidebar-nav">
      <NMenu
        :value="selectedKey"
        :options="menuOptions"
        :indent="16"
        @update:value="handleMenuUpdate"
      />
    </div>

    <div class="sidebar-footer">
      <div class="notification-entry" role="button" tabindex="0" @click="goToNotifications" @keydown.enter="goToNotifications">
        <NIcon size="18"><Notifications /></NIcon>
        <span class="notification-label">{{ t('nav.notifications') }}</span>
        <NBadge
          v-if="notificationStore.unreadCount > 0"
          :value="notificationStore.unreadCount"
          :max="99"
          class="notification-badge"
        />
      </div>

      <div class="theme-row">
        <span class="theme-label">{{ t('nav.darkMode') }}</span>
        <NSwitch :value="isDark" @update:value="setDark" size="small" />
      </div>

      <div class="theme-row">
        <span class="theme-label">{{ t('nav.language') }}</span>
        <NSelect
          :value="currentLocale"
          :options="SUPPORTED_LOCALES"
          size="small"
          style="width: 112px"
          @update:value="(v: AppLocale) => setLocale(v)"
        />
      </div>
      
      <div class="user-row">
        <div class="user-info-wrapper">
          <div class="user-info" role="button" tabindex="0" @click="router.push('/profile')" @keydown.enter="router.push('/profile')">
            <div class="user-avatar" :style="userAvatar ? { backgroundImage: `url(${userAvatar})`, backgroundSize: 'cover', backgroundPosition: 'center' } : {}">
              {{ userAvatar ? '' : userEmoji }}
            </div>
            <div class="user-detail">
              <div class="user-name">{{ auth.user?.display_name || auth.user?.username }}</div>
              <div class="user-role">
                <NTag size="tiny" :type="auth.isAdmin ? 'error' : auth.isStaff ? 'warning' : 'info'">
                  {{ auth.isAdmin ? t('common.role.superAdmin') : auth.isStaff ? t('common.role.admin') : t('common.role.user') }}
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
          {{ t('nav.logout') }}
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

/* ── Scrollable nav region (takes remaining height and scrolls when overflowing) ── */
.sidebar-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: var(--space-2) 0;
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
.theme-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2);
  border-radius: var(--radius);
}
.theme-label {
  font-size: var(--text-sm);
  color: var(--color-text);
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
}
.notification-badge {
  margin-left: 0;
}
/* The badge defaults to 12px and its height is driven by a theme variable, so a 1px change is invisible; here we shrink both
   font size and dimensions together to make the reduction clearly visible.
   Naive drives the font size via the --n-font-size variable, so we must set font-size directly with !important to override it;
   under scoped styles use :deep() to reach n-badge-sup. */
.notification-badge :deep(.n-badge-sup) {
  font-size: 10px !important;
  height: 16px;
  line-height: 16px;
  min-width: 16px;
  padding: 0 5px;
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

/* ── Menu: bold to strengthen navigation hierarchy (keep it refined and compact, do not change font size) ── */
/* The sidebar uses scoped styles; NMenu's internal nodes carry no scope attribute, so :deep() is needed to reach them */
.sidebar :deep(.n-menu-item-content) {
  font-weight: 600;
}

/* ── Unread answer red dot on the Chat menu label ──
   The label is built with h() render functions, whose elements do NOT carry
   the component's scoped-style attribute, so these rules must be global. */
:global(.menu-label-with-dot) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
:global(.menu-unread-dot) {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-danger, #e5484d);
  flex-shrink: 0;
}

/* ── Notification toast ── */
.notification-toast {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--color-primary);
  color: #fff;
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius);
  box-shadow: var(--shadow-lg);
  cursor: pointer;
  z-index: 10;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}
.notification-toast:hover {
  filter: brightness(1.1);
}
.toast-title {
  font-size: var(--text-sm);
  font-weight: 400;
  white-space: normal;
  overflow-wrap: anywhere;
}
.toast-content {
  font-size: var(--text-xs);
  opacity: 0.9;
  margin-top: 2px;
  white-space: normal;
  overflow-wrap: anywhere;
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
