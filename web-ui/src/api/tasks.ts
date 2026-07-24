import api from './client'

export const tasksApi = {
  list: (params?: { status?: string; type?: string; limit?: number }) =>
    api.get('/tasks/', { params }),
  create: (data: {
    type: string
    title?: string
    params?: Record<string, unknown>
    target_node_ids: string[]
  }) => api.post('/tasks/', data),
  get: (id: string) => api.get(`/tasks/${id}`),
  cancel: (id: string) => api.post(`/tasks/${id}/cancel`),
  retry: (id: string) => api.post(`/tasks/${id}/retry`),
  templates: () => api.get('/tasks/templates/list'),
}
