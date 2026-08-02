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
  created_at: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref<UserInfo | null>(null)
  const llmConfigured = ref(false)
  const contextWindow = ref(128000)  // LLM max context window (tokens), from /api/health

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isStaff = computed(() => user.value?.role === 'admin' || user.value?.role === 'moderator')

  function setAuth(t: string, u: UserInfo) {
    token.value = t
    user.value = u
    localStorage.setItem('token', t)
    client.defaults.headers.common['Authorization'] = `Bearer ${t}`
  }

  function clearAuth() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    delete client.defaults.headers.common['Authorization']
  }

  async function login(username: string, password: string) {
    const res = await client.post('/auth/login', { username, password })
    setAuth(res.data.access_token, res.data.user)
    return res.data
  }

  async function register(data: { username: string; password: string; display_name?: string }) {
    const res = await client.post('/auth/register', data)
    setAuth(res.data.access_token, res.data.user)
    return res.data
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
    } catch {
      clearAuth()
    }
  }

  function logout() {
    clearAuth()
    router.push('/login')
  }

  // Init from stored token
  if (token.value) {
    client.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
  }

  return { token, user, isLoggedIn, isAdmin, isStaff, llmConfigured, contextWindow, login, register, logout, fetchMe, refreshLlmStatus, setAuth, clearAuth }
})
