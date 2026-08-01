import api from './client'

export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
  refresh: () => api.post('/auth/refresh'),
  me: () => api.get('/auth/me'),
  logout: () => api.post('/auth/logout'),
  changePassword: (currentPassword: string, newPassword: string) =>
    api.post('/auth/password', { current_password: currentPassword, new_password: newPassword }),
  stepUp: (password: string) => api.post('/auth/step-up', { password }),
  revokeStepUp: () => api.delete('/auth/step-up'),
  createUser: (username: string, password: string, role: string = 'user') =>
    api.post('/auth/users', { username, password, role }),
  listUsers: () => api.get('/auth/users'),
  updateUser: (id: string, data: { role?: 'admin' | 'user'; disabled?: boolean }) =>
    api.patch(`/auth/users/${id}`, data),
}
