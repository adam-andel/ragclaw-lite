import axios from 'axios'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

const client = axios.create({
  baseURL: '/api',
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    // Auto-redirect to login on 401
    if (err.response?.status === 401) {
      const auth = useAuthStore()
      auth.clearAuth()
      router.push('/login')
      return Promise.reject(new Error('登录已过期，请重新登录'))
    }
    const msg = err.response?.data?.detail || err.message || '网络错误'
    console.error('[API Error]', msg)
    return Promise.reject(new Error(msg))
  },
)

export default client
