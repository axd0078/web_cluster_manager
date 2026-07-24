import api from './client'

export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
  refresh: () => api.post('/auth/refresh'),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.post('/auth/password', { current_password: currentPassword, new_password: newPassword }),
  createUser: (username: string, password: string, role: string = 'viewer') =>
    api.post('/auth/users', { username, password, role }),
}
