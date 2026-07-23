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

  // Refresh LLM config status from /api/health.
  //
  // The backend loads the .env LLM API key during async startup
  // (config_manager.init, run inside the app lifespan). The very first
  // /api/health response after a cold start may therefore report
  // llm_configured=false even though the key is actually present. To avoid
  // leaving the chat input permanently disabled until the user hits F5, we
  // poll for a short window and flip llmConfigured to true as soon as the
  // backend reports it.
  async function refreshLlmStatus(maxAttempts = 12, intervalMs = 1000) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      try {
        const hr = await fetch('/api/health')
        if (hr.ok) {
          const health = await hr.json()
          if (health.context_window) contextWindow.value = health.context_window
          if (health.llm_configured) {
            llmConfigured.value = true
            return
          }
        }
      } catch {
        // backend not ready yet — keep retrying
      }
      if (attempt < maxAttempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, intervalMs))
      }
    }
  }

  // Immediate single-shot status check. Used right after a settings save so the
  // chat input enables without waiting for the periodic poll below.
  async function checkLlmStatusNow() {
    try {
      const hr = await fetch('/api/health')
      if (hr.ok) {
        const health = await hr.json()
        if (health.context_window) contextWindow.value = health.context_window
        llmConfigured.value = !!health.llm_configured
      }
    } catch {
      // backend temporarily unreachable — ignore
    }
  }

  // Low-frequency periodic polling that covers runtime config changes — e.g.
  // the user sets the LLM API key a day after startup, or an admin changes it
  // while the chat is open. Once llmConfigured flips true it stops on its own.
  // Returns a stop() the caller must invoke on unmount.
  function startLlmStatusPolling(intervalMs = 15000): () => void {
    if (llmConfigured.value) return () => {}
    let stopped = false
    const timer = window.setInterval(async () => {
      if (stopped || llmConfigured.value) {
        stop()
        return
      }
      try {
        const hr = await fetch('/api/health')
        if (hr.ok) {
          const health = await hr.json()
          if (health.context_window) contextWindow.value = health.context_window
          if (health.llm_configured) {
            llmConfigured.value = true
            stop()
          }
        }
      } catch {
        // backend temporarily unreachable — retry next tick
      }
    }, intervalMs)
    function stop() {
      if (stopped) return
      stopped = true
      clearInterval(timer)
    }
    return stop
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
      llmConfigured.value = !!health.llm_configured
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

  return { token, user, isLoggedIn, isAdmin, isStaff, llmConfigured, contextWindow, login, register, logout, fetchMe, refreshLlmStatus, checkLlmStatusNow, startLlmStatusPolling, setAuth, clearAuth }
})
