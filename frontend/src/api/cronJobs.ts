import client from './client'
import type {
  CronJob,
  CronJobCreatePayload,
  CronJobUpdatePayload,
  CronJobListResponse,
  CronJobRunListResponse,
} from '@/types'

export const listCronJobs = (page = 1, size = 20, search?: string, isActive?: boolean) =>
  client.get<CronJobListResponse>('/cron-jobs', { params: { page, size, search, is_active: isActive } }).then(r => r.data)

export const getCronJob = (id: string) =>
  client.get<CronJob>(`/cron-jobs/${id}`).then(r => r.data)

export const createCronJob = (data: CronJobCreatePayload) =>
  client.post<CronJob>('/cron-jobs', data).then(r => r.data)

export const updateCronJob = (id: string, data: CronJobUpdatePayload) =>
  client.patch<CronJob>(`/cron-jobs/${id}`, data).then(r => r.data)

export const deleteCronJob = (id: string) =>
  client.delete(`/cron-jobs/${id}`).then(r => r.data)

export const toggleCronJob = (id: string) =>
  client.post<CronJob>(`/cron-jobs/${id}/toggle`).then(r => r.data)

export const runCronJobNow = (id: string) =>
  client.post<{ status: string; result?: string }>(`/cron-jobs/${id}/run-now`).then(r => r.data)

export const listCronJobRuns = (id: string, page = 1, size = 20) =>
  client.get<CronJobRunListResponse>(`/cron-jobs/${id}/runs`, { params: { page, size } }).then(r => r.data)
