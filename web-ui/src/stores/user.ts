import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '../api/auth'

const STEP_UP_PERMISSIONS = new Set([
  'tasks.high',
  'terminal.admin',
  'files.overwrite',
  'containers.control',
  'containers.logs',
  'nodes.manage',
  'groups.manage',
  'agents.manage',
  'brokers.manage',
  'users.manage',
  'alerts.manage',
  'updates.manage',
  'settings.write',
  'audit.export',
])

export interface CurrentUser {
  id: string
  username: string
  role: 'admin' | 'user'
  disabled: boolean
  permissions: string[]
  step_up_expires_at: string | null
}

export const useUserStore = defineStore('user', () => {
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)
  let stepUpRefreshTimer: ReturnType<typeof setTimeout> | null = null

  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const hasPermission = (permission: string) => {
    if (!user.value?.permissions.includes(permission)) return false
    if (!STEP_UP_PERMISSIONS.has(permission)) return true
    const expiresAt = user.value.step_up_expires_at
    return Boolean(expiresAt && Date.parse(expiresAt) > Date.now())
  }

  function scheduleStepUpRefresh() {
    if (stepUpRefreshTimer !== null) {
      clearTimeout(stepUpRefreshTimer)
      stepUpRefreshTimer = null
    }
    const expiresAt = user.value?.step_up_expires_at
    if (!expiresAt) return
    const delay = Date.parse(expiresAt) - Date.now() + 100
    if (delay <= 0) return
    stepUpRefreshTimer = setTimeout(() => {
      stepUpRefreshTimer = null
      void fetchUser(true)
    }, delay)
  }

  async function login(username: string, password: string) {
    const response = await authApi.login(username, password)
    user.value = response.data
    initialized.value = true
    scheduleStepUpRefresh()
  }

  async function fetchUser(force = false) {
    if (initialized.value && !force) return user.value
    try {
      const response = await authApi.me()
      user.value = response.data
      scheduleStepUpRefresh()
    } catch {
      user.value = null
      scheduleStepUpRefresh()
    } finally {
      initialized.value = true
    }
    return user.value
  }

  async function stepUp(password: string) {
    await authApi.stepUp(password)
    await fetchUser(true)
  }

  async function revokeStepUp() {
    await authApi.revokeStepUp()
    await fetchUser(true)
  }

  async function logout() {
    try {
      await authApi.logout()
    } finally {
      user.value = null
      initialized.value = true
      scheduleStepUpRefresh()
    }
  }

  return {
    user,
    initialized,
    isAuthenticated,
    isAdmin,
    hasPermission,
    login,
    fetchUser,
    stepUp,
    revokeStepUp,
    logout,
  }
})
