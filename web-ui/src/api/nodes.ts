import api from './client'

export const nodesApi = {
  list: (params?: { status?: string; group_id?: string; search?: string }) =>
    api.get('/nodes/', { params }),
  get: (id: string) => api.get(`/nodes/${id}`),
  register: (data: { ip: string; hostname?: string; os?: string; version?: string }) =>
    api.post('/nodes/', data),
  updateStatus: (id: string, status: string) =>
    api.put(`/nodes/${id}/status`, { status }),
  delete: (id: string) => api.delete(`/nodes/${id}`),
  getMetrics: (id: string, minutes?: number) =>
    api.get(`/nodes/${id}/metrics`, { params: { minutes } }),
  health: () => api.get('/nodes/health/summary'),
  containerAction: (containerId: string, action: 'start' | 'stop' | 'restart') =>
    api.post(`/containers/${containerId}/actions`, { action }),
  containerLogs: (containerId: string, tail = 200) =>
    api.get(`/containers/${containerId}/logs`, { params: { tail } }),

  // Groups
  listGroups: () => api.get('/nodes/groups/'),
  createGroup: (data: { name: string; description?: string; color?: string }) =>
    api.post('/nodes/groups/', data),
  deleteGroup: (id: string) => api.delete(`/nodes/groups/${id}`),
  addNodeToGroup: (groupId: string, nodeId: string) =>
    api.post(`/nodes/groups/${groupId}/nodes/${nodeId}`),
  removeNodeFromGroup: (groupId: string, nodeId: string) =>
    api.delete(`/nodes/groups/${groupId}/nodes/${nodeId}`),
}
