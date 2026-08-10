import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import client from '@/api/client'
import router from '@/router'

export interface UserInfo {
  id: string
  username: string
  display_name: string
  email: string | null
  role: string
  is_active: boolean
  avatar_url: string | null
  tenant_id: string | null
  memory: string
  created_at: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  // Refresh token kept in sessionStorage: cleared when the tab closes, limiting
  // the window an XSS-harvested token stays usable. It's still in storage (not
  // memory) so a transparent refresh survives a route change / HMR.
  const refreshToken = ref(sessionStorage.getItem('refreshToken') || '')
  const user = ref<UserInfo | null>(null)
  const llmConfigured = ref(false)
  const contextWindow = ref(128000)  // LLM max context window (tokens), from /api/health

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isStaff = computed(() => user.value?.role === 'admin' || user.value?.role === 'moderator')

  function setAuth(t: string, u: UserInfo, rt?: string) {
    token.value = t
    user.value = u
    localStorage.setItem('token', t)
    client.defaults.headers.common['Authorization'] = `Bearer ${t}`
    if (rt) {
      refreshToken.value = rt
      sessionStorage.setItem('refreshToken', rt)
    }
  }

  function clearAuth() {
    token.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem('token')
    sessionStorage.removeItem('refreshToken')
    delete client.defaults.headers.common['Authorization']
  }

  async function login(username: string, password: string) {
    const res = await client.post('/auth/login', { username, password })
    setAuth(res.data.access_token, res.data.user, res.data.refresh_token)
    return res.data
  }

  async function register(data: { username: string; password: string; display_name?: string }) {
    const res = await client.post('/auth/register', data)
    setAuth(res.data.access_token, res.data.user, res.data.refresh_token)
    return res.data
  }

  // Exchange the stored refresh token for a fresh access + refresh pair.
  // Returns true on success, false if the refresh token is invalid/expired
  // (caller should redirect to login). Throws on unexpected errors.
  async function refresh(): Promise<boolean> {
    if (!refreshToken.value) return false
    try {
      const res = await client.post('/auth/refresh', { refresh_token: refreshToken.value })
      // Persist the fresh tokens. The refresh response carries no user info, so
      // we keep the existing cached user. If user is somehow null we fall back
      // to re-fetching the profile below. `as UserInfo` is safe here: even if
      // user.value is null at this instant, the /auth/me call below restores it
      // before any consumer observes the null state.
      setAuth(res.data.access_token, user.value as UserInfo, res.data.refresh_token)
      // If we had no cached user (e.g. the page loaded, then the access token
      // expired before fetchMe ran), re-fetch the profile now that we hold a
      // fresh, valid access token. Without this, user would be left null and
      // `isLoggedIn` would flip to false, ejecting the user to the login view.
      if (!user.value) {
        try {
          const me = await client.get('/auth/me')
          setAuth(token.value!, me.data, refreshToken.value)
        } catch {
          /* best-effort; keep whatever state we have */
        }
      }
      return true
    } catch {
      return false
    }
  }

  // Public: whether the system still has zero users (first admin not yet
  // registered). Drives the login page's register-vs-login mode on first launch.
  async function needsSetup() {
    const res = await client.get('/auth/setup')
    return !!res.data.needs_setup
  }

  // Refresh LLM reachability status from /api/health with backoff.
  //
  // llmConfigured now means the LLM API is *actually reachable* (verified by a
  // real request), not merely that a key is present. The backend verifies
  // reachability on demand when /api/health is polled (and caches the result),
  // so polling flips llmConfigured to true as soon as the API answers — even if
  // the key was configured well after startup. If the key is wrong/unreachable
  // the chat input stays disabled until a working config is saved via Settings.
  //
  // Backoff: first probe is delayed 1s after the call; if it fails, wait 2s for
  // the next, then 3s, 4s, ... (interval grows by 1s per failed attempt). Up to
  // 10 attempts total, then give up (the chat input stays disabled).
  async function refreshLlmStatus(maxAttempts = 10) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      // Delay grows with each failed attempt: 1s, 2s, 3s, ... (0-indexed).
      await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 1000))
      try {
        const hr = await fetch('/api/health')
        if (hr.ok) {
          const health = await hr.json()
          if (health.context_window) contextWindow.value = health.context_window
          if (health.llm_reachable) {
            llmConfigured.value = true
            return
          }
        }
      } catch {
        // backend not ready yet — keep retrying with backoff
      }
    }
  }

  async function fetchMe() {
    if (!token.value) return
    try {
      client.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
      const res = await client.get('/auth/me')
      user.value = res.data
      // Single immediate status check; ChatView also runs refreshLlmStatus()
      // (polling) so the input self-enables once the backend finishes startup.
      const hr = await fetch('/api/health')
      const health = await hr.json()
      llmConfigured.value = !!health.llm_reachable
      if (health.context_window) contextWindow.value = health.context_window
    } catch (e: any) {
      // Do NOT clearAuth() here. A 401 (expired access token) is already
      // handled by the client response interceptor, which refreshes the token
      // and retries; if the refresh also fails it logs the user out there.
      // Wiping auth on transient errors (network blip, 5xx) here would destroy
      // the refresh token and break silent renewal, forcing a re-login.
      // Only treat a genuine auth failure as terminal (interceptor owns logout).
      const status = e?.response?.status
      if (status === 401) {
        // interceptor already attempted refresh+retry; if we still land here the
        // session is truly dead. Avoid clobbering tokens on non-auth errors.
        if (!token.value) clearAuth()
      }
    }
  }

  async function logout() {
    // Best-effort server-side revocation of the current refresh token.
    if (refreshToken.value) {
      try {
        await client.post('/auth/logout', { refresh_token: refreshToken.value })
      } catch {
        // ignore network errors — local clear is what matters
      }
    }
    clearAuth()
    router.push('/login')
  }

  // Init from stored token
  if (token.value) {
    client.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
  }

  return { token, refreshToken, user, isLoggedIn, isAdmin, isStaff, llmConfigured, contextWindow, login, register, needsSetup, logout, fetchMe, refreshLlmStatus, setAuth, clearAuth, refresh }
})
