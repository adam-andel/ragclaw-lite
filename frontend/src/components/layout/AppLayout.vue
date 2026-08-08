<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, h } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NIcon, NDrawer } from 'naive-ui'
import { Menu } from '@vicons/ionicons5'
import Sidebar from './Sidebar.vue'
import ChatView from '@/views/ChatView.vue'
import { useNotificationStore } from '@/stores/notifications'

const route = useRoute()
const notificationStore = useNotificationStore()
const isMobile = ref(false)
const drawerOpen = ref(false)

// ChatView is mounted once the user first visits any chat route and then KEPT in
// the DOM for the whole session — we only toggle its visibility (visibility:hidden,
// never display:none) so the browser preserves scroll position, draft input and all
// in-component state naturally across page switches.
const chatMounted = ref(false)
watch(
  () => route.meta.keepAlive,
  (k) => { if (k) chatMounted.value = true },
  { immediate: true },
)
// True while the active route is a chat route; drives which layer is visible.
const isChatRoute = computed(() => !!route.meta.keepAlive)

function checkMobile() {
  isMobile.value = window.innerWidth < 768
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
  notificationStore.startPolling(5000)
})
onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
  notificationStore.stopPolling()
})

// Close drawer on navigation
watch(() => route.path, () => {
  drawerOpen.value = false
})
</script>

<template>
  <div class="app-layout">
    <!-- Desktop sidebar -->
    <Sidebar v-if="!isMobile" />

    <!-- Mobile hamburger + drawer -->
    <template v-if="isMobile">
      <NButton class="mobile-menu-btn" size="small" @click="drawerOpen = true">
        <template #icon><NIcon size="20"><Menu /></NIcon></template>
      </NButton>
      <NDrawer v-model:show="drawerOpen" placement="left" :width="280">
        <Sidebar />
      </NDrawer>
    </template>

    <main class="main-content">
      <router-view v-slot="{ Component, route }">
        <!-- ChatView is mounted once and KEPT in the DOM for the whole session. We hide
             it with `visibility:hidden` (NOT display:none) so the browser preserves its
             scroll position, draft input and all in-component state across page switches. -->
        <ChatView v-if="chatMounted" class="chat-layer" :class="{ 'chat-layer--hidden': !isChatRoute }" />
        <!-- Non-chat routes render normally and replace the (hidden) chat view. -->
        <component v-if="!isChatRoute" :is="Component" :key="route.fullPath" class="page-layer" />
      </router-view>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  overscroll-behavior: none;
}
.main-content {
  position: relative;
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: var(--space-6) var(--space-8);
  background: var(--color-bg);
}
/* ChatView hidden layer: kept laid-out (visibility, NOT display) so the browser
   preserves its scroll position; absolutely positioned to stay out of normal flow.
   Two classes (.chat-layer.chat-layer--hidden) out-specify ChatView's own
   .chat-view { position: relative } so this wins regardless of style injection order. */
.chat-layer.chat-layer--hidden {
  position: absolute;
  inset: 0;
  visibility: hidden;
  pointer-events: none;
}
.mobile-menu-btn {
  position: fixed;
  top: var(--space-2);
  left: var(--space-2);
  z-index: 100;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

@media (max-width: 767px) {
  .main-content {
    padding: var(--space-3);
    padding-top: var(--space-12);
  }
}
</style>
