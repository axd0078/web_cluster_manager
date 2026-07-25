import api from './client'

export interface TaskTargets {
  target_node_ids: string[]
  target_group_ids: string[]
  all_online: boolean
}

export interface TaskCreatePayload extends TaskTargets {
  type: string
  title?: string
  params: Record<string, unknown>
}

export const tasksApi = {
  list: (params?: { status?: string; task_type?: string; limit?: number }) =>
    api.get('/tasks/', { params }),
  create: (data: TaskCreatePayload) => api.post('/tasks/', data),
  resolveTargets: (data: TaskTargets) => api.post('/tasks/targets/resolve', data),
  get: (id: string) => api.get(`/tasks/${id}`),
  cancel: (id: string) => api.post(`/tasks/${id}/cancel`),
  retry: (id: string, targetNodeIds?: string[]) =>
    api.post(`/tasks/${id}/retry`, { target_node_ids: targetNodeIds ?? null }),
  templates: () => api.get('/tasks/templates/list'),
}
