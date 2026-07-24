import axios, { type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

function readCookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`
  const item = document.cookie.split('; ').find(value => value.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : ''
}

const api = axios.create({
  baseURL: '/api/v2',
  timeout: 15000,
  withCredentials: true,
})

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const method = (config.method || 'get').toLowerCase()
  if (['post', 'put', 'patch', 'delete'].includes(method)) {
    const csrf = readCookie('wcm_csrf')
    if (csrf) config.headers.set('X-CSRF-Token', csrf)
  }
  return config
})

let refreshing: Promise<void> | null = null

api.interceptors.response.use(
  response => response,
  async error => {
    const response = error.response
    const config = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined
    const isAuthRequest = String(config?.url || '').includes('/auth/')
    if (response?.status === 401 && config && !config._retry && !isAuthRequest) {
      config._retry = true
      if (!refreshing) {
        refreshing = axios.post('/api/v2/auth/refresh', null, {
          withCredentials: true,
          headers: { 'X-CSRF-Token': readCookie('wcm_csrf') },
        }).then(() => undefined).finally(() => { refreshing = null })
      }
      try {
        await refreshing
        return api(config)
      } catch {
        if (window.location.pathname !== '/login') window.location.href = '/login'
      }
    } else if (response?.data?.detail) {
      ElMessage.error(response.data.detail)
    }
    return Promise.reject(error)
  },
)

export default api
