import client from './client'
import type { SearchResult } from '@/types'

export interface SearchParams {
  query: string
  kb_id?: string
  vector_weight?: number
  bm25_weight?: number
  top_k?: number
  threshold?: number
}

export const search = (params: SearchParams) =>
  client.post<SearchResult[]>('/retrieval/search', params)
