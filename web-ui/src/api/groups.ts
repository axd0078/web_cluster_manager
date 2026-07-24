import api from './client'

export const groupsApi = {
  list: () => api.get('/nodes/groups/'),
  create: (data: { name: string; description?: string; color?: string }) =>
    api.post('/nodes/groups/', data),
  delete: (id: string) => api.delete(`/nodes/groups/${id}`),
  addNode: (groupId: string, nodeId: string) =>
    api.post(`/nodes/groups/${groupId}/nodes/${nodeId}`),
  removeNode: (groupId: string, nodeId: string) =>
    api.delete(`/nodes/groups/${groupId}/nodes/${nodeId}`),
}
