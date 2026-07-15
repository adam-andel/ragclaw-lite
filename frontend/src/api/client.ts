import axios from 'axios'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import { i18n } from '@/i18n'

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
      const e = new Error(detail || i18n.global.t('errors.loginExpired'))
      e.response = err.response
      return Promise.reject(e)
    }
    const msg = detail || err.message || i18n.global.t('errors.networkError')
    console.error('[API Error]', msg)
    const e = new Error(msg)
    e.response = err.response
    return Promise.reject(e)
  },
)

export default client
