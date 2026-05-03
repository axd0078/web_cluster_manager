import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const role = ref(localStorage.getItem('role') || '')

  const isLoggedIn = computed(() => !!token.value)

  function setAuth(access: string, refresh: string, user: string, r: string) {
    token.value = access
    refreshToken.value = refresh
    username.value = user
    role.value = r
    localStorage.setItem('access_token', access)
    localStorage.setItem('refresh_token', refresh)
    localStorage.setItem('username', user)
    localStorage.setItem('role', r)
  }

  function logout() {
    token.value = ''
    refreshToken.value = ''
    username.value = ''
    role.value = ''
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('username')
    localStorage.removeItem('role')
  }

  return { token, refreshToken, username, role, isLoggedIn, setAuth, logout }
})
