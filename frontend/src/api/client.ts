import axios from 'axios'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'

const client = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail
    if (err.response?.status === 401) {
      // Don't redirect if we're on the login page (bad credentials case)
      if (router.currentRoute.value.path !== '/login') {
        const auth = useAuthStore()
        auth.clearAuth()
        router.push('/login')
      }
      // Preserve server detail, or fallback to generic message
      return Promise.reject(new Error(detail || '登录已过期，请重新登录'))
    }
    const msg = detail || err.message || '网络错误'
    console.error('[API Error]', msg)
    return Promise.reject(new Error(msg))
  },
)

export default client
