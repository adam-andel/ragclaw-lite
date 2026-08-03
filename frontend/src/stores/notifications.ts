import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  listNotifications,
  getUnreadNotificationCount,
  markNotificationAsRead,
  markAllNotificationsAsRead,
  deleteNotification as deleteNotificationApi,
  type ListNotificationsParams,
} from '@/api/notifications'
import type { NotificationItem } from '@/types'
import router from '@/router'
import { useBrowserNotification } from '@/composables/useBrowserNotification'
import { useChatUnreadStore } from '@/stores/chatUnread'

// Matches backend hardcoded English cron-job notification patterns.
// title: "Cron job executed: <name>" / "Cron job failed: <name>"
// content: "Task executed" / "Task failed"
const CRON_EXECUTED_RE = /^Cron job executed: (.+)$/
const CRON_FAILED_RE = /^Cron job failed: (.+)$/
const CRON_TASK_EXECUTED = 'Task executed'
const CRON_TASK_FAILED = 'Task failed'

interface CronI18nMeta {
  titleKey: string
  titleParams: Record<string, string>
  contentKey: string | null
}

function extractCronI18nMeta(item: NotificationItem): CronI18nMeta | null {
  if (item.type !== 'cron_job') return null

  const m = item.title.match(CRON_EXECUTED_RE)
  if (m) {
    return {
      titleKey: 'notifications.cron.executed',
      titleParams: { name: m[1] },
      contentKey: item.content === CRON_TASK_EXECUTED ? 'notifications.cron.taskExecuted' : null,
    }
  }
  const mf = item.title.match(CRON_FAILED_RE)
  if (mf) {
    return {
      titleKey: 'notifications.cron.failed',
      titleParams: { name: mf[1] },
      contentKey: item.content === CRON_TASK_FAILED ? 'notifications.cron.taskFailed' : null,
    }
  }
  return null
}

/**
 * Store original (backend-English) items and annotate cron-job items with i18n
 * meta so that a downstream computed can reactively translate them whenever
 * the locale changes.
 */
function tagCronI18nMeta(item: NotificationItem): NotificationItem {
  const meta = extractCronI18nMeta(item)
  if (!meta) return item
  return { ...item, _cronI18n: meta } as NotificationItem
}

/**
 * Translate a single tagged notification item into the current locale.
 * Non-cron items pass through unchanged.
 */
function translateCronNotification(item: NotificationItem, t: (key: string, params?: Record<string, string>) => string): NotificationItem {
  const meta = (item as any)._cronI18n as CronI18nMeta | undefined
  if (!meta) return item
  return {
    ...item,
    title: t(meta.titleKey, meta.titleParams),
    content: meta.contentKey ? t(meta.contentKey) : item.content,
  }
}

export const useNotificationStore = defineStore('notifications', () => {
  const { t } = useI18n({ useScope: 'global' })
  const unreadCount = ref(0)
  const _notifications = ref<NotificationItem[]>([])
  const total = ref(0)
  const _latestUnread = ref<NotificationItem | null>(null)
  const toastVisible = ref(false)
  const loading = ref(false)

  // Reactively translated notification list: cron-job titles/contents follow the
  // active locale.  The raw list (_notifications) stores backend-English strings
  // plus _cronI18n meta; this computed re-evaluates every time the locale changes
  // (vue-i18n's t() is reactive).
  const notifications = computed(() =>
    _notifications.value.map(item => translateCronNotification(item, t)),
  )

  // Same reactive translation for the sidebar toast notification.
  const latestUnread = computed(() => {
    const raw = _latestUnread.value
    return raw ? translateCronNotification(raw, t) : null
  })

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let hideToastTimer: ReturnType<typeof setTimeout> | null = null

  // Browser desktop notification: only sent when the user has granted permission; lastNotifiedId is used for deduplication
  const { notify: notifyBrowser } = useBrowserNotification()
  let lastNotifiedId: string | null = null
  // Whether the baseline (newest existing unread) has been recorded. The first
  // poll only seeds this baseline so we never notify for notifications that
  // already existed before the page loaded.
  let seeded = false

  const hasUnread = computed(() => unreadCount.value > 0)

  async function fetchUnreadCount() {
    try {
      const data = await getUnreadNotificationCount()
      unreadCount.value = data.unread_count
    } catch (e) {
      console.error('[Notifications] fetch unread count failed', e)
    }
  }

  async function fetchNotifications(params: ListNotificationsParams = {}) {
    loading.value = true
    try {
      const data = await listNotifications(params)
      _notifications.value = data.items.map(item => tagCronI18nMeta(item))
      total.value = data.total
      unreadCount.value = data.unread_count
      return data
    } catch (e) {
      console.error('[Notifications] fetch notifications failed', e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function markAsRead(id: string) {
    try {
      await markNotificationAsRead(id)
      const item = _notifications.value.find(n => n.id === id)
      if (item && !item.read) {
        item.read = true
        item.read_at = new Date().toISOString()
        unreadCount.value = Math.max(0, unreadCount.value - 1)
        // Trigger computed reactivity (mutating an array element's property
        // does not change the ref value, so the computed won't re-evaluate).
        _notifications.value = [..._notifications.value]
      }
      if (_latestUnread.value?.id === id) {
        _latestUnread.value = null
      }
    } catch (e) {
      console.error('[Notifications] mark as read failed', e)
    }
  }

  async function markAllRead() {
    try {
      await markAllNotificationsAsRead()
      _notifications.value.forEach(n => {
        n.read = true
        n.read_at = new Date().toISOString()
      })
      unreadCount.value = 0
      _latestUnread.value = null
      // Trigger computed reactivity.
      _notifications.value = [..._notifications.value]
    } catch (e) {
      console.error('[Notifications] mark all read failed', e)
    }
  }

  async function deleteNotification(id: string) {
    try {
      await deleteNotificationApi(id)
      const idx = _notifications.value.findIndex(n => n.id === id)
      if (idx !== -1) {
        if (!_notifications.value[idx].read) {
          unreadCount.value = Math.max(0, unreadCount.value - 1)
        }
        _notifications.value.splice(idx, 1)
        total.value = Math.max(0, total.value - 1)
      }
      if (_latestUnread.value?.id === id) {
        _latestUnread.value = null
      }
    } catch (e) {
      console.error('[Notifications] delete failed', e)
      throw e
    }
  }

  function showToast(notification: NotificationItem) {
    // Store raw (with _cronI18n meta) so the computed latestUnread can
    // reactively re-translate when the locale switches.
    _latestUnread.value = notification
    toastVisible.value = true
    if (hideToastTimer) clearTimeout(hideToastTimer)
    hideToastTimer = setTimeout(() => {
      toastVisible.value = false
    }, 3000)
  }

  function hideToast() {
    toastVisible.value = false
    if (hideToastTimer) {
      clearTimeout(hideToastTimer)
      hideToastTimer = null
    }
  }

  async function poll() {
    const previousCount = unreadCount.value
    await fetchUnreadCount()

    // First poll: only record the current newest unread as the baseline, so we
    // don't toast/notify for notifications that already existed at page load.
    if (!seeded) {
      seeded = true
      try {
        const data = await listNotifications({ page: 1, size: 1, unreadOnly: true })
        lastNotifiedId = data.items[0]?.id ?? null
      } catch (e) {
        console.error('[Notifications] seed latest unread failed', e)
      }
      return
    }

    if (unreadCount.value > previousCount) {
      try {
        const data = await listNotifications({ page: 1, size: 1, unreadOnly: true })
        if (data.items.length > 0) {
          const raw = tagCronI18nMeta(data.items[0])
          if (raw.id !== lastNotifiedId) {
            lastNotifiedId = raw.id
            showToast(raw)
            fireBrowserNotification(translateCronNotification(raw, t))
          }
        }
      } catch (e) {
        console.error('[Notifications] poll latest unread failed', e)
      }
    }
  }

  // Surface a browser notification whenever a chat "unread" red dot appears —
  // i.e. an assistant answer finished streaming, OR a tool-call round-limit
  // pause happened, for a conversation the user is NOT currently viewing.
  // `markUnread` is the single entry point that produces every such red dot
  // (the sidebar Chat label, the history-button dot in ChatView, and the
  // per-conversation "有未读消息" badge), so we watch the underlying id list and
  // notify for EACH newly-unread conversation — not just the first one. This way
  // a second red dot still notifies even if another is already showing.
  // Sending itself is gated inside fireBrowserNotification by Notification.permission
  // === 'granted', i.e. only when the user has enabled browser notifications.
  const chatUnread = useChatUnreadStore()
  let prevUnreadIds = new Set<string>()
  watch(
    () => chatUnread.unreadConvIds,
    (ids) => {
      const newIds = ids.filter(id => !prevUnreadIds.has(id))
      prevUnreadIds = new Set(ids)
      for (const id of newIds) {
        fireBrowserNotification({
          id: `chat-unread-${id}`,
          title: 'ragclaw',
          content: t('chat.hasUnread'),
          link: `/chat/${id}`,
        })
      }
    },
    { deep: true },
  )

  // When a new unread arrives, push a browser desktop notification only if the user has ALREADY granted permission.
  // Do not request permission during polling: permission requests must be triggered by a user gesture (see the notification center page button).
  function fireBrowserNotification(item: NotificationItem) {
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
    const instance = notifyBrowser(item.title, {
      body: item.content || '',
      tag: `ragclaw-notify-${item.id}`,
    })
    if (instance) {
      instance.onclick = () => {
        window.focus()
        const target = item.link || '/notifications'
        router.push(target).catch(() => {})
        instance.close()
      }
    }
  }

  function startPolling(intervalMs = 5000) {
    stopPolling()
    poll()
    pollTimer = setInterval(poll, intervalMs)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return {
    unreadCount,
    notifications,
    total,
    latestUnread,
    toastVisible,
    loading,
    hasUnread,
    fetchUnreadCount,
    fetchNotifications,
    markAsRead,
    markAllRead,
    deleteNotification,
    showToast,
    hideToast,
    poll,
    startPolling,
    stopPolling,
  }
})
