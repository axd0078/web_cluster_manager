import api from './client'

export const updatesApi = {
  listPackages: () => api.get('/updates/packages'),
  createPackage: (params: { version: string; description?: string; file?: File }) => {
    const form = new FormData()
    if (params.file) form.append('file', params.file)
    return api.post('/updates/packages', form, {
      params: { version: params.version, description: params.description },
      headers: params.file ? { 'Content-Type': 'multipart/form-data' } : undefined,
    })
  },
  pushUpdate: (params: { package_id: string; target_node_ids: string[] }) =>
    api.post('/updates/push', null, { params }),
  getNodeVersion: (nodeId: string) => api.get(`/updates/nodes/${nodeId}/version`),
}
