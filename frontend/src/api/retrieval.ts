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

export interface RetrievalConfig {
  vector_weight: number | null
  bm25_weight: number | null
  vector_top_k: number | null
  bm25_top_k: number | null
  final_top_k: number | null
  similarity_threshold: number | null
}

export const getKbRetrievalConfig = (kbId: string) =>
  client.get<RetrievalConfig>(`/kb/${kbId}/retrieval-config`)

export const updateKbRetrievalConfig = (kbId: string, config: Partial<RetrievalConfig>) =>
  client.put<RetrievalConfig>(`/kb/${kbId}/retrieval-config`, config)