// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
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

export const deleteNotification = (id: string) =>
  client.delete<{ id: string; deleted: boolean }>(`/notifications/${id}`).then(r => r.data)
