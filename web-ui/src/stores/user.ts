import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '../api/auth'

export interface CurrentUser {
  id: string
  username: string
  role: 'admin' | 'operator' | 'viewer'
}

export const useUserStore = defineStore('user', () => {
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)

  // Authentication is represented by the server-managed HttpOnly cookie.
  // The browser cannot and should not read that cookie directly, so user
  // state is restored through /auth/me instead of localStorage tokens.
  const isAuthenticated = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function login(username: string, password: string) {
    const response = await authApi.login(username, password)
    user.value = response.data
    initialized.value = true
  }

  async function fetchUser(force = false) {
    if (initialized.value && !force) return user.value
    try {
      const response = await authApi.me()
      user.value = response.data
    } catch {
      user.value = null
    } finally {
      initialized.value = true
    }
    return user.value
  }

  async function logout() {
    try {
      await authApi.logout()
    } finally {
      user.value = null
      initialized.value = true
    }
  }

  return { user, initialized, isAuthenticated, isAdmin, login, fetchUser, logout }
})
