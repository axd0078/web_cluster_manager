import axios from 'axios'
import { useUserStore } from '../stores/user'
import router from '../router'

const client = axios.create({
  baseURL: '/api/v2',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Request interceptor — inject Bearer token
client.interceptors.request.use((config) => {
  const userStore = useUserStore()
  if (userStore.token) {
    config.headers.Authorization = `Bearer ${userStore.token}`
  }
  return config
})

// Response interceptor — handle 401
client.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      const userStore = useUserStore()
      // Try refresh
      if (userStore.refreshToken) {
        try {
          const res = await axios.post('/api/v2/auth/refresh', {
            refresh_token: userStore.refreshToken,
          })
          const { access_token, refresh_token, user } = res.data
          userStore.setAuth(access_token, refresh_token, user.username, user.role)
          // Retry original request
          error.config.headers.Authorization = `Bearer ${access_token}`
          return client(error.config)
        } catch {
          userStore.logout()
          router.push('/login')
        }
      } else {
        userStore.logout()
        router.push('/login')
      }
    }
    return Promise.reject(error)
  }
)

export default client
