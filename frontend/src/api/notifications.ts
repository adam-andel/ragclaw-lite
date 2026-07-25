import client from './client'
import type { NotificationItem, NotificationListResponse } from '@/types'

export interface ListNotificationsParams {
  page?: number
  size?: number
  unreadOnly?: boolean
  search?: string
  type?: string
  read?: boolean | null
}

export const listNotifications = (params: ListNotificationsParams = {}) => {
  const { page = 1, size = 20, unreadOnly = false, search, type, read } = params
  const query: Record<string, unknown> = { page, size, unread_only: unreadOnly }
  if (search) query.search = search
  if (type) query.type = type
  if (read !== undefined && read !== null) query.read = read
  return client.get<NotificationListResponse>('/notifications', { params: query }).then(r => r.data)
}

export const getUnreadNotificationCount = () =>
  client.get<{ unread_count: number }>('/notifications/unread-count').then(r => r.data)

export const markNotificationAsRead = (id: string) =>
  client.patch<{ id: string; read: boolean }>(`/notifications/${id}/read`).then(r => r.data)

export const markAllNotificationsAsRead = () =>
  client.patch<{ marked_as_read: number }>('/notifications/read-all').then(r => r.data)
