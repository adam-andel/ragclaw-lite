import client from './client'
import type { PluginListResponse } from '@/types'

export const listPlugins = () =>
  client.get<PluginListResponse>('/plugins').then(r => r.data)

export const enablePlugin = (name: string) =>
  client.post<{ name: string; enabled: boolean }>(`/plugins/${name}/enable`).then(r => r.data)

export const disablePlugin = (name: string, reason?: string) =>
  client.post<{ name: string; enabled: boolean }>(
    `/plugins/${name}/disable`, { reason }
  ).then(r => r.data)

export const refreshPluginCache = () =>
  client.post<{ ok: boolean }>('/plugins/refresh-cache').then(r => r.data)
