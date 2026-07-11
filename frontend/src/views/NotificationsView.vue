<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NCard, NDataTable, NTag, NSpace, NButton, NSpin, NEmpty,
  NIcon,
} from 'naive-ui'
import { CheckmarkDone } from '@vicons/ionicons5'
import { useNotificationStore } from '@/stores/notifications'
import AppPagination from '@/components/common/AppPagination.vue'
import type { NotificationItem } from '@/types'
import type { DataTableColumns } from 'naive-ui'

const notificationStore = useNotificationStore()
const page = ref(1)
const size = ref(20)
const unreadOnly = ref(false)

const columns: DataTableColumns<NotificationItem> = [
  {
    title: '类型',
    key: 'type',
    width: 120,
    render: (row: NotificationItem) => h(
      NTag,
      { type: row.type === 'cron_job' ? 'success' : 'default', size: 'small' },
      { default: () => row.type === 'cron_job' ? '定时任务' : '系统' },
    ),
  },
  {
    title: '标题',
    key: 'title',
    ellipsis: { tooltip: true },
    render: (row: NotificationItem) => h(
      'span',
      { style: { fontWeight: row.read ? 'normal' : '600' } },
      row.title,
    ),
  },
  {
    title: '内容',
    key: 'content',
    ellipsis: { tooltip: true },
  },
  {
    title: '时间',
    key: 'created_at',
    width: 170,
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render: (row: NotificationItem) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          !row.read
            ? h(NButton, { size: 'tiny', onClick: () => notificationStore.markAsRead(row.id) }, {
                default: () => '标记已读',
              })
            : h(NTag, { type: 'default', size: 'small' }, { default: () => '已读' }),
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
    <NCard title="通知中心">
      <template #header-extra>
        <NSpace>
          <NButton size="small" @click="toggleUnreadOnly">
            {{ unreadOnly ? '显示全部' : '仅未读' }}
          </NButton>
          <NButton size="small" type="primary" :disabled="!notificationStore.hasUnread" @click="notificationStore.markAllRead">
            <template #icon><NIcon><CheckmarkDone /></NIcon></template>
            全部已读
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
          <NEmpty description="暂无通知" />
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
