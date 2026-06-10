import client from './client'
import type { SystemStats } from '@/types'

export const getOverview = () => client.get<SystemStats>('/stats/overview')
