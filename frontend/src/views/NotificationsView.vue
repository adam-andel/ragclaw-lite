<script setup lang="ts">
import { ref, onMounted, h, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  NCard, NDataTable, NTag, NButton, NSpin, NEmpty,
  NIcon, NPagination, NInput, NSelect, NPopconfirm,
} from 'naive-ui'
import { CheckmarkDone, Notifications, Search, Trash, Open } from '@vicons/ionicons5'
import { useNotificationStore } from '@/stores/notifications'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import { useBrowserNotification } from '@/composables/useBrowserNotification'
import type { NotificationItem } from '@/types'
import type { DataTableColumns } from 'naive-ui'

const { t } = useI18n()
const notificationStore = useNotificationStore()
const router = useRouter()
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

// Format ISO timestamp as YYYY-MM-DD HH:mm:ss (no milliseconds).
function formatTime(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const selected = ref<NotificationItem | null>(null)
const showDetail = ref(false)

const columns: DataTableColumns<NotificationItem> = [
  {
    title: t('notifications.title'),
    key: 'title',
    minWidth: 240,
    render: (row: NotificationItem) => h(
      'span',
      { class: 'nt-title-cell', onClick: () => openDetail(row) },
      [
        !row.read ? h('span', { class: 'nt-dot' }) : null,
        h('span', { class: 'nt-title-text' }, row.title),
      ],
    ),
  },
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
    title: t('common.time'),
    key: 'created_at',
    width: 170,
    render: (row: NotificationItem) => h('span', formatTime(row.created_at)),
  },
  {
    title: t('common.action'),
    key: 'actions',
    width: 90,
    render: (row: NotificationItem) => {
      if (row.read) {
        return h(NButton, { size: 'tiny', type: 'error', tertiary: true, onClick: () => onDelete(row) }, {
          default: () => t('common.delete'),
        })
      }
      return h(
        NPopconfirm,
        { onPositiveClick: () => onDelete(row) },
        {
          trigger: () =>
            h(NButton, { size: 'tiny', type: 'error', tertiary: true }, { default: () => t('common.delete') }),
          default: () => t('common.confirmDelete'),
        },
      )
    },
  },
]

function rowClassName(row: NotificationItem): string {
  return row.read ? 'nt-row-read' : ''
}

// Clicking a title only opens the modal (identical to the working CronJobsView
// pattern). We deliberately do NOT mark-as-read here: doing so mutates the list
// and re-renders the table while the modal is mid-open, which interrupts the
// modal and leaves it invisible. Instead we mark as read once the modal closes.
function openDetail(row: NotificationItem) {
  selected.value = row
  showDetail.value = true
}

// Extract the cron job id from a notification link like
// "/cron-jobs?job=<id>&logs=1" (older notifications may just be "/cron-jobs").
function cronJobIdFromLink(link?: string | null): string | null {
  if (!link) return null
  try {
    const url = new URL(link, window.location.origin)
    return url.searchParams.get('job')
  } catch {
    return null
  }
}

// Open the linked cron job's detail modal with logs, equivalent to clicking the
// "Logs" button inside the CronJobsView detail modal.
function viewCronJobDetail() {
  const jobId = cronJobIdFromLink(selected.value?.link)
  if (!jobId) return
  showDetail.value = false
  router.push({ path: '/cron-jobs', query: { job: jobId, logs: '1' } }).catch(() => {})
}

// Mark the viewed notification as read after the modal has closed, so the list
// re-render can never interfere with the modal's open transition.
watch(showDetail, (open) => {
  if (!open && selected.value && !selected.value.read) {
    notificationStore.markAsRead(selected.value.id)
  }
})

async function onDelete(row: NotificationItem) {
  try {
    await notificationStore.deleteNotification(row.id)
    if (showDetail.value && selected.value?.id === row.id) {
      showDetail.value = false
    }
  } catch (e) {
    // error already logged in the store
  }
}

function onDeleteSelected() {
  if (selected.value) onDelete(selected.value)
}

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
          :row-class-name="rowClassName"
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

    <AppModal
      v-model:show="showDetail"
      :title="t('notifications.detail')"
      size="detail"
    >
      <div v-if="selected" class="nt-detail">
        <h3 class="nt-detail-title">{{ selected.title }}</h3>
        <div class="nt-detail-meta">
          <NTag :type="selected.type === 'cron_job' ? 'success' : 'default'" size="small">
            {{ selected.type === 'cron_job' ? t('notifications.type.cron') : t('notifications.type.system') }}
          </NTag>
          <span class="nt-detail-time">{{ formatTime(selected.created_at) }}</span>
          <NButton
            v-if="selected.type === 'cron_job' && cronJobIdFromLink(selected.link)"
            size="tiny"
            secondary
            type="primary"
            @click="viewCronJobDetail"
          >
            <template #icon><NIcon><Open /></NIcon></template>
            {{ t('notifications.viewCronJob') }}
          </NButton>
        </div>
        <div class="nt-detail-content">
          <p class="nt-detail-body">{{ selected.content || '-' }}</p>
        </div>
      </div>
      <template #footer>
        <div class="nt-detail-footer">
          <NButton v-if="selected && selected.read" type="error" @click="onDeleteSelected">
            <template #icon><NIcon><Trash /></NIcon></template>
            {{ t('common.delete') }}
          </NButton>
          <NPopconfirm v-else @positive-click="onDeleteSelected">
            <template #trigger>
              <NButton type="error">
                <template #icon><NIcon><Trash /></NIcon></template>
                {{ t('common.delete') }}
              </NButton>
            </template>
            {{ t('common.confirmDelete') }}
          </NPopconfirm>
        </div>
      </template>
    </AppModal>
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
.nt-table :deep(.n-data-table-th) {
  text-align: center;
}
.nt-table :deep(tr.nt-row-read td) {
  color: var(--color-text-muted);
}
.nt-table :deep(.nt-title-cell) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  cursor: pointer;
}
.nt-table :deep(.nt-dot) {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-error, #d03050);
  flex-shrink: 0;
}
.nt-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.nt-detail-title {
  margin: 0;
  font-weight: 600;
  font-size: inherit;
  line-height: 1.5;
  word-break: break-word;
}
.nt-detail-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--color-text-muted);
  font-size: 0.85rem;
}
.nt-detail-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.nt-detail-body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}
.nt-detail-footer {
  display: flex;
  justify-content: flex-end;
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
