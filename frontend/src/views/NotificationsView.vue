<script setup lang="ts">
import { ref, onMounted, h, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NCard, NDataTable, NTag, NSpace, NButton, NSpin, NEmpty,
  NIcon, NPagination, NInput, NSelect,
} from 'naive-ui'
import { CheckmarkDone, Notifications, Search } from '@vicons/ionicons5'
import { useNotificationStore } from '@/stores/notifications'
import PageHeader from '@/components/common/PageHeader.vue'
import { useBrowserNotification } from '@/composables/useBrowserNotification'
import type { NotificationItem } from '@/types'
import type { DataTableColumns } from 'naive-ui'

const { t } = useI18n()
const notificationStore = useNotificationStore()
const { permission, requestPermission, supported } = useBrowserNotification()
const page = ref(1)
const size = ref(20)
const search = ref('')
const filterType = ref<string>('all')
const filterRead = ref<string>('all')

const typeOptions = [
  { label: t('common.allTypes'), value: 'all' },
  { label: t('notifications.type.cron'), value: 'cron_job' },
  { label: t('notifications.type.system'), value: 'system' },
]
const readOptions = [
  { label: t('notifications.filter.allStatus'), value: 'all' },
  { label: t('notifications.filter.read'), value: 'read' },
  { label: t('notifications.filter.unread'), value: 'unread' },
]

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
  await notificationStore.fetchNotifications({
    page: page.value,
    size: size.value,
    search: search.value.trim() || undefined,
    type: filterType.value !== 'all' ? filterType.value : undefined,
    read: filterRead.value === 'all' ? undefined : filterRead.value === 'read',
  })
}

function onSearch() {
  page.value = 1
  load()
}
function resetFilters() {
  search.value = ''
  filterType.value = 'all'
  filterRead.value = 'all'
  page.value = 1
  load()
}

// 页数据变少导致当前页越界时自动收敛。
watch(() => notificationStore.total, (total) => {
  const totalPages = Math.max(1, Math.ceil(total / size.value))
  if (page.value > totalPages) page.value = totalPages
})

onMounted(load)
</script>

<template>
  <div class="notifications-view">
    <PageHeader :title="t('notifications.center')" :icon="Notifications">
      <template #actions>
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

        <NButton size="small" type="primary" :disabled="!notificationStore.hasUnread" @click="notificationStore.markAllRead">
          <template #icon><NIcon><CheckmarkDone /></NIcon></template>
          {{ t('notifications.markAllRead') }}
        </NButton>
      </template>
    </PageHeader>

    <div class="nt-filters">
      <NInput v-model:value="search" :placeholder="t('notifications.searchPlaceholder')" clearable size="small" style="flex:1" @keyup.enter="onSearch">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterType" :options="typeOptions" :placeholder="t('common.type')" size="small" style="width:130px" @update:value="onSearch" />
      <NSelect v-model:value="filterRead" :options="readOptions" :placeholder="t('common.status')" size="small" style="width:120px" @update:value="onSearch" />
      <NButton size="small" secondary @click="resetFilters">{{ t('common.reset') }}</NButton>
    </div>

    <NCard class="nt-card" :bordered="false" :content-style="{ padding: '4px 12px 12px' }">
      <NSpin :show="notificationStore.loading">
        <NEmpty
          v-if="!notificationStore.loading && notificationStore.total === 0"
          :description="t('notifications.empty')"
          class="nt-empty"
        />
        <NDataTable
          v-else
          :columns="columns"
          :data="notificationStore.notifications"
          :row-key="(row: NotificationItem) => row.id"
          :bordered="false"
          size="small"
          class="nt-table"
        />
      </NSpin>
    </NCard>

    <div v-if="notificationStore.total > 0" class="nt-pagination">
      <NPagination
        v-model:page="page"
        :page-size="size"
        :item-count="notificationStore.total"
        @update:page="load"
      />
    </div>
  </div>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.nt-filters {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.nt-card {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.nt-table {
  --n-th-text-color: var(--color-text-muted);
  --n-td-text-color: var(--color-text);
}
.nt-empty {
  padding: 64px 0;
}
.nt-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 10px 4px 0;
  flex-wrap: wrap;
}
.nt-pagination-info {
  font-size: 0.78rem;
  color: var(--color-text-muted);
}
</style>
