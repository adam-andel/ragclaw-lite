import client from './client'
import type {
  Skill, SkillCreatePayload, SkillUpdatePayload,
  SkillToolBindPayload, SkillToolBindResult, SkillListResponse,
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

export const bindTool = (skillId: string, data: SkillToolBindPayload) =>
  client.post<SkillToolBindResult>(`/skills/${skillId}/tools`, data).then(r => r.data)

export const unbindTool = (skillId: string, toolId: string) =>
  client.delete(`/skills/${skillId}/tools/${toolId}`).then(r => r.data)
