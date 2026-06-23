import client from './client'
import type { KnowledgeBase, DocumentItem, ChunkItem, DocumentListResponse, DocKBLinkResponse, KBUpdatePayload } from '@/types'

// ─── Knowledge Bases ───

export const listKnowledgeBases = () => client.get<KnowledgeBase[]>('/kb')

export const createKnowledgeBase = (data: { name: string; description?: string }) =>
  client.post<KnowledgeBase>('/kb', data)

export const updateKnowledgeBase = (id: string, data: KBUpdatePayload) =>
  client.patch<KnowledgeBase>(`/kb/${id}`, data)

export const deleteKnowledgeBase = (id: string) => client.delete(`/kb/${id}`)

// ─── KB Document Linking (m2m) ───

export const listKBDocuments = (kbId: string) =>
  client.get<DocumentItem[]>(`/kb/${kbId}/documents`)

export const addDocumentsToKB = (kbId: string, docIds: string[]) =>
  client.post<DocKBLinkResponse>(`/kb/${kbId}/documents`, { doc_ids: docIds })

export const removeDocumentFromKB = (kbId: string, docId: string) =>
  client.delete(`/kb/${kbId}/documents/${docId}`)

// ─── Documents ───

export const uploadDocument = (file: File, onProgress?: (pct: number) => void) => {
  const form = new FormData()
  form.append('file', file)
  return client.post<DocumentItem>('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (e.total && onProgress) onProgress(Math.round((e.loaded * 100) / e.total))
    },
  })
}

export const uploadDocumentsBatch = (files: File[], onProgress?: (pct: number) => void) => {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  return client.post<DocumentItem[]>('/documents/upload/batch', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (e.total && onProgress) onProgress(Math.round((e.loaded * 100) / e.total))
    },
  })
}

export const listAllDocuments = (params?: {
  page?: number; size?: number; status?: string
  file_type?: string; search?: string
}) => client.get<DocumentListResponse>('/documents', { params })

export const getDocument = (id: string) => client.get<DocumentItem>(`/documents/${id}`)

export const getDocumentStatus = (id: string) =>
  client.get<{ id: string; status: string; error_message?: string; chunk_count: number; progress: number }>(`/documents/${id}/status`)

export const getDocumentChunks = (id: string) => client.get<ChunkItem[]>(`/documents/${id}/chunks`)

export const getDocumentKBs = (id: string) => client.get<string[]>(`/documents/${id}/kbs`)

export const deleteDocument = (id: string) => client.delete(`/documents/${id}`)

// Legacy: list documents by KB (backward compat for old KB page)
export const listDocuments = (kbId: string) =>
  client.get<DocumentItem[]>(`/documents/by-kb/${kbId}`)
