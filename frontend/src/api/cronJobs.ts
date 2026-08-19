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
import client from './client'
import type {
  CronJob,
  CronJobCreatePayload,
  CronJobUpdatePayload,
  CronJobListResponse,
  CronJobRunListResponse,
} from '@/types'

export const listCronJobs = (page = 1, size = 20, search?: string, status?: string) =>
  client.get<CronJobListResponse>('/cron-jobs', { params: { page, size, search, status } }).then(r => r.data)

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

export const resetCronJob = (id: string) =>
  client.post<CronJob>(`/cron-jobs/${id}/reset`).then(r => r.data)

export const runCronJobNow = (id: string) =>
  client.post<{ status: string; result?: string }>(`/cron-jobs/${id}/run-now`).then(r => r.data)

export const listCronJobRuns = (id: string, page = 1, size = 20) =>
  client.get<CronJobRunListResponse>(`/cron-jobs/${id}/runs`, { params: { page, size } }).then(r => r.data)
