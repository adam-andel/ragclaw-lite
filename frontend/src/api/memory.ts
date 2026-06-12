import client from './client'

export interface MemoryItem {
  id?: string
  memory?: string
  text?: string
  score?: number
  created_at?: string
  updated_at?: string
}

export const searchMemories = (q: string) =>
  client.get<MemoryItem[]>('/memory', { params: { q } })

export const listMemories = () =>
  client.get<MemoryItem[]>('/memory')

export const deleteMemory = (id: string) =>
  client.delete(`/memory/${id}`)
