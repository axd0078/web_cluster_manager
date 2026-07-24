import api from './client'

export const monitorApi = {
  current: () => api.get('/monitor/current'),
  alerts: (params?: { limit?: number; resolved?: boolean }) =>
    api.get('/monitor/alerts', { params }),
  listAlerts: (params?: { limit?: number; severity?: string; resolved?: boolean }) =>
    api.get('/alerts/', { params }),
  resolveAlert: (alertId: string) => api.post(`/alerts/${alertId}/resolve`),
  listRules: () => api.get('/alerts/rules'),
  createRule: (params: {
    name: string; metric: string; condition: string;
    threshold: number; duration?: number; channels?: string
  }) => api.post('/alerts/rules', null, { params }),
  updateRule: (ruleId: string, params: Record<string, unknown>) =>
    api.put(`/alerts/rules/${ruleId}`, null, { params }),
}
