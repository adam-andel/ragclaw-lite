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
import type {
  MCPServer, MCPServerCreatePayload, MCPServerUpdatePayload,
  MCPServerListResponse, MCPServerTestResult,
} from '@/types'

export const listServers = (page = 1, size = 20, search?: string, isActive?: boolean, includeBuiltin = true) =>
  client.get<MCPServerListResponse>('/mcp/servers', { params: { page, size, search, is_active: isActive, include_builtin: includeBuiltin } }).then(r => r.data)

export const getServer = (id: string) =>
  client.get<MCPServer>(`/mcp/servers/${id}`).then(r => r.data)

export const createServer = (data: MCPServerCreatePayload) =>
  client.post<MCPServer>('/mcp/servers', data).then(r => r.data)

export const updateServer = (id: string, data: MCPServerUpdatePayload) =>
  client.patch<MCPServer>(`/mcp/servers/${id}`, data).then(r => r.data)

export const deleteServer = (id: string) =>
  client.delete(`/mcp/servers/${id}`).then(r => r.data)

export const testServer = (id: string) =>
  client.post<MCPServerTestResult>(`/mcp/servers/${id}/test`).then(r => r.data)

export const refreshTools = () =>
  client.post<{ status: string; servers: number; total_tools: number }>('/mcp/servers/refresh-tools').then(r => r.data)
