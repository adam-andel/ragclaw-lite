import axios, { type AxiosError, type AxiosResponse } from 'axios'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import { i18n } from '@/i18n'

/** Error enriched with the originating axios response so callers can inspect status/payload. */
interface ApiError extends Error {
  response?: AxiosResponse
}

const client = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// --- Transparent token refresh on 401 ---
// A single in-flight refresh is shared across all concurrent 401s so we don't
// fire N refresh requests at once. Requests that arrive while a refresh is
// pending wait on the same promise, then retry with the new access token.
let refreshing: Promise<boolean> | null = null

function doRefresh(): Promise<boolean> {
  if (!refreshing) {
    const auth = useAuthStore()
    refreshing = auth.refresh().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

// Mark the refresh endpoint itself so its own 401 (refresh expired) does not
// trigger a recursive refresh loop — it falls through to the login redirect.
function isRefreshRequest(cfg: any): boolean {
  return cfg?.__isRefresh === true || cfg?.url === '/auth/refresh'
}

client.interceptors.response.use(
  (res) => res,
  async (err: AxiosError) => {
    const body = err.response?.data as { detail?: string } | undefined
    const detail = body?.detail

    if (err.response?.status === 401) {
      const cfg = err.config as (import('axios').InternalAxiosRequestConfig & { _retry?: boolean }) | undefined

      // Try one transparent refresh + retry, unless this IS the refresh call
      // (its 401 means the refresh token is dead → go straight to login).
      if (cfg && !cfg._retry && !isRefreshRequest(cfg)) {
        try {
          const ok = await doRefresh()
          if (ok) {
            cfg._retry = true
            const auth = useAuthStore()
            cfg.headers = cfg.headers || {}
            cfg.headers['Authorization'] = `Bearer ${auth.token}`
            return client.request(cfg)
          }
        } catch {
          // refresh threw → fall through to login redirect below
        }
      }

      // Don't redirect if we're on the login page (bad credentials case)
      if (router.currentRoute.value.path !== '/login') {
        const auth = useAuthStore()
        auth.clearAuth()
        router.push('/login')
      }
      // Preserve server detail, or fallback to generic message
      const e: ApiError = new Error(detail || i18n.global.t('errors.loginExpired'))
      e.response = err.response
      return Promise.reject(e)
    }

    const msg = detail || err.message || i18n.global.t('errors.networkError')
    console.error('[API Error]', msg)
    const e: ApiError = new Error(msg)
    e.response = err.response
    return Promise.reject(e)
  },
)

export default client
