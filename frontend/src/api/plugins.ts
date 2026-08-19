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
