import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 120000, // 2 min for uploads
  headers: { 'Content-Type': 'application/json' },
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.detail || err.message || '网络错误'
    console.error('[API Error]', msg)
    return Promise.reject(new Error(msg))
  },
)

export default client
