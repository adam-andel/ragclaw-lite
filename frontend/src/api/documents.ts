import client from './client'
import type { KnowledgeBase, DocumentItem, ChunkItem } from '@/types'

// Knowledge Bases
export const listKnowledgeBases = () => client.get<KnowledgeBase[]>('/kb')
export const createKnowledgeBase = (data: { name: string; description?: string }) =>
  client.post<KnowledgeBase>('/kb', data)
export const deleteKnowledgeBase = (id: string) => client.delete(`/kb/${id}`)

// Documents
export const uploadDocument = (kbId: string, file: File, onProgress?: (pct: number) => void) => {
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', kbId)
  return client.post<DocumentItem>('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (e.total && onProgress) onProgress(Math.round((e.loaded * 100) / e.total))
    },
  })
}

export const getDocumentStatus = (id: string) => client.get<DocumentItem>(`/documents/${id}/status`)
export const getDocumentChunks = (id: string) => client.get<ChunkItem[]>(`/documents/${id}/chunks`)
export const listDocuments = (kbId: string) => client.get<DocumentItem[]>(`/documents`, { params: { kb_id: kbId } })
export const deleteDocument = (id: string) => client.delete(`/documents/${id}`)
