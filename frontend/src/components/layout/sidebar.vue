<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NIcon, NButton, NTag, NPopconfirm, NEmpty, NTooltip } from 'naive-ui'
import {
  Chatbubbles, FolderOpen, Search, StatsChart,
  LogOut, People, Add, Trash, ChevronDown,
} from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { deleteConversation, authHeaders } from '@/api/chat'

interface ConvItem { id: string; title: string; updated_at: string }

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const isOnChatRoute = computed(() => route.path.startsWith('/chat'))

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
  if (key === '/chat') {
    router.push('/chat').then(() => {
      window.dispatchEvent(new CustomEvent('erag:reset-chat'))
    })
  } else {
    router.push(key)
  }
}

// ── Conversations ──

const conversations = ref<ConvItem[]>([])
const conversationId = computed(() => route.params.id as string | undefined)
const convExpanded = ref(true)
const initialLoadDone = ref(false)

async function loadConversations() {
  try {
    const uid = auth.user?.id || ''
    const r = await fetch(`/api/conversations?user_id=${uid}`, { headers: authHeaders() })
    if (!r.ok) throw new Error('Failed')
    conversations.value = await r.json()

    // Auto-navigate to first conversation when landing on /chat without an id
    if (!initialLoadDone.value && !conversationId.value && conversations.value.length > 0) {
      initialLoadDone.value = true
      router.replace(`/chat/${conversations.value[0].id}`)
    }
    initialLoadDone.value = true
  } catch {
    conversations.value = []
  }
}

function selectConversation(id: string) {
  router.push(`/chat/${id}`)
}

async function handleDelete(id: string) {
  try {
    await deleteConversation(id)
    if (conversationId.value === id) {
      router.replace('/chat')
      window.dispatchEvent(new CustomEvent('erag:reset-chat'))
    }
    await loadConversations()
  } catch { /* noop */ }
}

function newConversation() {
  router.replace('/chat')
  window.dispatchEvent(new CustomEvent('erag:reset-chat'))
}

// Refresh when a conversation is created/updated in ChatView
function onConversationUpdated() {
  loadConversations()
}

// Refresh on route enter
watch(() => route.path, (path) => {
  if (path.startsWith('/chat')) {
    loadConversations()
  }
})

onMounted(() => {
  if (isOnChatRoute.value) loadConversations()
  window.addEventListener('erag:conversation-updated', onConversationUpdated)
})
onUnmounted(() => {
  window.removeEventListener('erag:conversation-updated', onConversationUpdated)
})
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

    <!-- Conversation history – collapsible, only on /chat -->
    <div v-if="isOnChatRoute" class="conv-section">
      <div
        class="conv-section-header"
        role="button"
        tabindex="0"
        :aria-expanded="convExpanded"
        aria-controls="conv-list"
        @click="convExpanded = !convExpanded"
        @keydown.enter="convExpanded = !convExpanded"
        @keydown.space.prevent="convExpanded = !convExpanded"
      >
        <span class="conv-section-title">对话历史</span>
        <div class="conv-section-actions">
          <NButton size="tiny" @click.stop="newConversation">
            <template #icon><NIcon><Add /></NIcon></template>
          </NButton>
          <NIcon size="16" :class="{ rotated: !convExpanded }" class="chevron-icon">
            <ChevronDown />
          </NIcon>
        </div>
      </div>
      <div id="conv-list" v-show="convExpanded" class="conv-list" role="list" aria-label="对话历史列表">
        <NEmpty v-if="conversations.length === 0" description="暂无对话" style="padding: var(--space-3)" />
        <div
          v-for="c in conversations"
          :key="c.id"
          :class="['conv-item', { active: c.id === conversationId }]"
          role="button"
          tabindex="0"
          :aria-current="c.id === conversationId ? 'location' : undefined"
          @click="selectConversation(c.id)"
          @keydown.enter="selectConversation(c.id)"
          @keydown.space.prevent="selectConversation(c.id)"
        >
          <div class="conv-item-text">
            <span class="conv-name">{{ c.title || '新对话' }}</span>
            <span class="conv-time">{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) }}</span>
          </div>
          <div class="conv-item-actions">
            <NPopconfirm @positive-click="handleDelete(c.id)" positive-text="确认" negative-text="取消">
              <template #trigger>
                <NTooltip>
                  <template #trigger>
                    <NButton text size="tiny" type="error" @click.stop>
                      <NIcon size="14"><Trash /></NIcon>
                    </NButton>
                  </template>
                  删除对话
                </NTooltip>
              </template>
              确定删除此对话？
            </NPopconfirm>
          </div>
        </div>
      </div>
    </div>

    <div class="sidebar-footer">
      <div class="user-info">
        <div class="user-avatar">👤</div>
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

/* ── Conversation section ── */
.conv-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-top: 1px solid var(--color-border);
  margin-top: var(--space-2);
  overflow: hidden;
}
.conv-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-5) var(--space-2);
  cursor: pointer;
  user-select: none;
  flex-shrink: 0;
}
.conv-section-header:hover {
  background: var(--color-primary-soft);
}
.conv-section-title {
  font-weight: 600;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.conv-section-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.chevron-icon {
  transition: transform 0.2s;
  color: var(--color-text-muted);
}
.chevron-icon.rotated {
  transform: rotate(-90deg);
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  padding-bottom: var(--space-2);
}
.conv-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px var(--space-5);
  cursor: pointer;
  transition: background .15s;
}
.conv-item:hover {
  background: var(--color-primary-soft);
}
.conv-item.active {
  background: rgba(79, 110, 247, 0.1);
}
.conv-item-text {
  flex: 1;
  min-width: 0;
  margin-right: var(--space-1);
}
.conv-item-actions {
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
  flex-shrink: 0;
}
.conv-item:hover .conv-item-actions {
  opacity: 1;
  pointer-events: auto;
}
.conv-name {
  display: block;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-time {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

/* ── Footer ── */
.sidebar-footer {
  margin-top: auto;
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
