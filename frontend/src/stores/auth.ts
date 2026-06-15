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
  tenant_id: string | null
  created_at: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref<UserInfo | null>(null)

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

  async function fetchMe() {
    if (!token.value) return
    try {
      client.defaults.headers.common['Authorization'] = `Bearer ${token.value}`
      const res = await client.get('/auth/me')
      user.value = res.data
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

  return { token, user, isLoggedIn, isAdmin, isStaff, login, register, logout, fetchMe, setAuth, clearAuth }
})
