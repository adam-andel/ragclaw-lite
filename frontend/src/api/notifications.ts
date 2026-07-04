import client from './client'
import type { NotificationItem, NotificationListResponse } from '@/types'

export const listNotifications = (page = 1, size = 20, unreadOnly = false) =>
  client.get<NotificationListResponse>('/notifications', { params: { page, size, unread_only: unreadOnly } }).then(r => r.data)

export const getUnreadNotificationCount = () =>
  client.get<{ unread_count: number }>('/notifications/unread-count').then(r => r.data)

export const markNotificationAsRead = (id: string) =>
  client.patch<{ id: string; read: boolean }>(`/notifications/${id}/read`).then(r => r.data)

export const markAllNotificationsAsRead = () =>
  client.patch<{ marked_as_read: number }>('/notifications/read-all').then(r => r.data)
