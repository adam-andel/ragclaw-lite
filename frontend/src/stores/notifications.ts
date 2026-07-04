import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  listNotifications,
  getUnreadNotificationCount,
  markNotificationAsRead,
  markAllNotificationsAsRead,
} from '@/api/notifications'
import type { NotificationItem } from '@/types'

export const useNotificationStore = defineStore('notifications', () => {
  const unreadCount = ref(0)
  const notifications = ref<NotificationItem[]>([])
  const total = ref(0)
  const latestUnread = ref<NotificationItem | null>(null)
  const toastVisible = ref(false)
  const loading = ref(false)

  let pollTimer: ReturnType<typeof setInterval> | null = null
  let hideToastTimer: ReturnType<typeof setTimeout> | null = null

  const hasUnread = computed(() => unreadCount.value > 0)

  async function fetchUnreadCount() {
    try {
      const data = await getUnreadNotificationCount()
      unreadCount.value = data.unread_count
    } catch (e) {
      console.error('[Notifications] fetch unread count failed', e)
    }
  }

  async function fetchNotifications(page = 1, size = 20, unreadOnly = false) {
    loading.value = true
    try {
      const data = await listNotifications(page, size, unreadOnly)
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
    if (unreadCount.value > previousCount) {
      try {
        const data = await listNotifications(1, 1, true)
        if (data.items.length > 0) {
          showToast(data.items[0])
        }
      } catch (e) {
        console.error('[Notifications] poll latest unread failed', e)
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
    showToast,
    hideToast,
    poll,
    startPolling,
    stopPolling,
  }
})
