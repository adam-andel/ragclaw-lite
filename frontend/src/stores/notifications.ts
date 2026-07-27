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

export const useNotificationStore = defineStore('notifications', () => {
  const unreadCount = ref(0)
  const notifications = ref<NotificationItem[]>([])
  const total = ref(0)
  const latestUnread = ref<NotificationItem | null>(null)
  const toastVisible = ref(false)
  const loading = ref(false)

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
      notifications.value = data.items
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
      const item = notifications.value.find(n => n.id === id)
      if (item && !item.read) {
        item.read = true
        item.read_at = new Date().toISOString()
        unreadCount.value = Math.max(0, unreadCount.value - 1)
      }
      if (latestUnread.value?.id === id) {
        latestUnread.value = null
      }
    } catch (e) {
      console.error('[Notifications] mark as read failed', e)
    }
  }

  async function markAllRead() {
    try {
      await markAllNotificationsAsRead()
      notifications.value.forEach(n => {
        n.read = true
        n.read_at = new Date().toISOString()
      })
      unreadCount.value = 0
      latestUnread.value = null
    } catch (e) {
      console.error('[Notifications] mark all read failed', e)
    }
  }

  async function deleteNotification(id: string) {
    try {
      await deleteNotificationApi(id)
      const idx = notifications.value.findIndex(n => n.id === id)
      if (idx !== -1) {
        if (!notifications.value[idx].read) {
          unreadCount.value = Math.max(0, unreadCount.value - 1)
        }
        notifications.value.splice(idx, 1)
        total.value = Math.max(0, total.value - 1)
      }
      if (latestUnread.value?.id === id) {
        latestUnread.value = null
      }
    } catch (e) {
      console.error('[Notifications] delete failed', e)
      throw e
    }
  }

  function showToast(notification: NotificationItem) {
    latestUnread.value = notification
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
          const item = data.items[0]
          if (item.id !== lastNotifiedId) {
            lastNotifiedId = item.id
            showToast(item)
            fireBrowserNotification(item)
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
  const { t } = useI18n({ useScope: 'global' })
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
