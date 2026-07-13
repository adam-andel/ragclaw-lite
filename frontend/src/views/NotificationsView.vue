<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NCard, NDataTable, NTag, NSpace, NButton, NSpin, NEmpty,
  NIcon,
} from 'naive-ui'
import { CheckmarkDone } from '@vicons/ionicons5'
import { useNotificationStore } from '@/stores/notifications'
import AppPagination from '@/components/common/AppPagination.vue'
import { useBrowserNotification } from '@/composables/useBrowserNotification'
import type { NotificationItem } from '@/types'
import type { DataTableColumns } from 'naive-ui'

const { t } = useI18n()
const notificationStore = useNotificationStore()
const { permission, requestPermission, supported } = useBrowserNotification()
const page = ref(1)
const size = ref(20)
const unreadOnly = ref(false)

const columns: DataTableColumns<NotificationItem> = [
  {
    title: t('common.type'),
    key: 'type',
    width: 120,
    render: (row: NotificationItem) => h(
      NTag,
      { type: row.type === 'cron_job' ? 'success' : 'default', size: 'small' },
      { default: () => row.type === 'cron_job' ? t('notifications.type.cron') : t('notifications.type.system') },
    ),
  },
  {
    title: t('notifications.title'),
    key: 'title',
    ellipsis: { tooltip: true },
    render: (row: NotificationItem) => h(
      'span',
      { style: { fontWeight: row.read ? 'normal' : '600' } },
      row.title,
    ),
  },
  {
    title: t('notifications.content'),
    key: 'content',
    ellipsis: { tooltip: true },
  },
  {
    title: t('common.time'),
    key: 'created_at',
    width: 170,
  },
  {
    title: t('common.action'),
    key: 'actions',
    width: 120,
    render: (row: NotificationItem) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          !row.read
            ? h(NButton, { size: 'tiny', onClick: () => notificationStore.markAsRead(row.id) }, {
                default: () => t('notifications.markRead'),
              })
            : h(NTag, { type: 'default', size: 'small' }, { default: () => t('notifications.read') }),
        ],
      }),
  },
]

async function load() {
  await notificationStore.fetchNotifications(page.value, size.value, unreadOnly.value)
}

function toggleUnreadOnly() {
  unreadOnly.value = !unreadOnly.value
  page.value = 1
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <NCard :title="t('notifications.center')">
      <template #header-extra>
        <NSpace>
          <NTag v-if="permission === 'granted'" type="success" size="small" :bordered="false">
            {{ t('notifications.desktop.on') }}
          </NTag>
          <NButton
            v-else-if="permission === 'default' && supported"
            size="small"
            @click="requestPermission"
          >
            {{ t('notifications.desktop.enable') }}
          </NButton>
          <NTag v-else-if="permission === 'denied'" type="warning" size="small" :bordered="false">
            {{ t('notifications.desktop.blocked') }}
          </NTag>
          <NTag v-else type="default" size="small" :bordered="false">
            {{ t('notifications.desktop.unsupported') }}
          </NTag>

          <NButton size="small" @click="toggleUnreadOnly">
            {{ unreadOnly ? t('notifications.showAll') : t('notifications.unreadOnly') }}
          </NButton>
          <NButton size="small" type="primary" :disabled="!notificationStore.hasUnread" @click="notificationStore.markAllRead">
            <template #icon><NIcon><CheckmarkDone /></NIcon></template>
            {{ t('notifications.markAllRead') }}
          </NButton>
        </NSpace>
      </template>

      <NSpin :show="notificationStore.loading">
        <NDataTable
          :columns="columns"
          :data="notificationStore.notifications"
          :row-key="(row: NotificationItem) => row.id"
          striped
        />
        <div v-if="notificationStore.total === 0" class="empty">
          <NEmpty :description="t('notifications.empty')" />
        </div>
        <AppPagination
          v-else
          :page="page"
          :page-size="size"
          :item-count="notificationStore.total"
          :page-sizes="[20, 50, 100]"
          show-size-picker
          :always-show="true"
          align="end"
          @update:page="(p: number) => { page = p; load() }"
          @update:page-size="(s: number) => { size = s; load() }"
        />
      </NSpin>
    </NCard>
  </div>
</template>

<style scoped>
.empty {
  padding: 48px 0;
}
</style>
