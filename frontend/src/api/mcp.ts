import client from './client'
import type {
  MCPServer, MCPServerCreatePayload, MCPServerUpdatePayload,
  MCPServerListResponse, MCPServerTestResult,
} from '@/types'

export const listServers = (page = 1, size = 20, search?: string) =>
  client.get<MCPServerListResponse>('/mcp/servers', { params: { page, size, search } }).then(r => r.data)

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
