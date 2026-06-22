<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, h } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NIcon, NDrawer } from 'naive-ui'
import { Menu } from '@vicons/ionicons5'
import Sidebar from './Sidebar.vue'

const route = useRoute()
const isMobile = ref(false)
const drawerOpen = ref(false)

function checkMobile() {
  isMobile.value = window.innerWidth < 768
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})
onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
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
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}
.main-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6) var(--space-8);
  background: var(--color-bg);
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
