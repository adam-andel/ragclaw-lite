import client from './client'
import type {
  Skill, SkillCreatePayload, SkillUpdatePayload,
  SkillListResponse,
  ResourceListResponse, ResourceUploadResponse, SyncResponse,
} from '@/types'

export const listSkills = (page = 1, size = 20, search?: string) =>
  client.get<SkillListResponse>('/skills', { params: { page, size, search } }).then(r => r.data)

export const getSkill = (id: string) =>
  client.get<Skill>(`/skills/${id}`).then(r => r.data)

export const createSkill = (data: SkillCreatePayload) =>
  client.post<Skill>('/skills', data).then(r => r.data)

export const updateSkill = (id: string, data: SkillUpdatePayload) =>
  client.patch<Skill>(`/skills/${id}`, data).then(r => r.data)

export const deleteSkill = (id: string) =>
  client.delete(`/skills/${id}`).then(r => r.data)

export const toggleSkill = (id: string) =>
  client.patch<Skill>(`/skills/${id}/toggle`).then(r => r.data)

// ── Folder upload (webkitdirectory) ──
export const uploadFolder = (files: File[], paths: string[]) => {
  const formData = new FormData()
  files.forEach(f => formData.append('files', f))
  paths.forEach(p => formData.append('paths', p))
  return client.post<Skill>('/skills/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── ZIP upload ──
export const uploadZip = (file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return client.post<Skill>('/skills/upload-zip', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Re-upload (replace existing skill folder) ──
export const reuploadFolder = (id: string, files: File[], paths: string[]) => {
  const formData = new FormData()
  files.forEach(f => formData.append('files', f))
  paths.forEach(p => formData.append('paths', p))
  return client.post<Skill>(`/skills/${id}/reupload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const reuploadZip = (id: string, file: File) => {
  const formData = new FormData()
  formData.append('file', file)
  return client.post<Skill>(`/skills/${id}/reupload-zip`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ── Resource management ──
export const listResources = (skillId: string) =>
  client.get<ResourceListResponse>(`/skills/${skillId}/resources`).then(r => r.data)

export const uploadResource = (skillId: string, subdir: string, file: File) => {
  const formData = new FormData()
  formData.append('subdir', subdir)
  formData.append('file', file)
  return client.post<ResourceUploadResponse>(`/skills/${skillId}/resources`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

export const deleteResource = (skillId: string, subdir: string, filename: string) =>
  client.delete(`/skills/${skillId}/resources/${subdir}/${filename}`).then(r => r.data)

// ── Sync ──
export const syncSkills = () =>
  client.post<SyncResponse>('/skills/sync').then(r => r.data)
