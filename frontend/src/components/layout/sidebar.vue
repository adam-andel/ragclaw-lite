<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { computed, h } from 'vue'
import { NMenu, NIcon } from 'naive-ui'
import { Chatbubbles, FolderOpen, Search, StatsChart } from '@vicons/ionicons5'
import type { MenuOption } from 'naive-ui'

const route = useRoute()
const router = useRouter()

const menuOptions: MenuOption[] = [
  { label: '对话', key: '/chat', icon: () => h(NIcon, null, { default: () => h(Chatbubbles) }) },
  { label: '知识库', key: '/knowledge', icon: () => h(NIcon, null, { default: () => h(FolderOpen) }) },
  { label: '检索调试', key: '/debug', icon: () => h(NIcon, null, { default: () => h(Search) }) },
  { label: '仪表盘', key: '/dashboard', icon: () => h(NIcon, null, { default: () => h(StatsChart) }) },
]

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
      <span class="text-xs text-gray-400">EnterpriseRAG v0.1</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 20px 20px 16px;
  border-bottom: 1px solid var(--color-border);
}
.logo {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--color-primary);
}
.version {
  font-size: 0.7rem;
  color: var(--color-text-muted);
  background: var(--color-border);
  padding: 1px 6px;
  border-radius: 4px;
}
.sidebar-footer {
  margin-top: auto;
  padding: 12px 20px;
  border-top: 1px solid var(--color-border);
}
</style>
