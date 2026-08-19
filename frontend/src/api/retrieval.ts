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
﻿import client from './client'
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