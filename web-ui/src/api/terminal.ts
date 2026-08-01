import api from './client'

export interface TerminalNodeCapability {
  node_id: string
  hostname: string
  platform: string
  low_profiles: string[]
  low_available: boolean
  broker_online: boolean
  admin_available: boolean
  protocol_compatible: boolean
}

export interface TerminalCapabilities {
  secure_transport: boolean
  low_enabled: boolean
  privileged_enabled: boolean
  nodes: TerminalNodeCapability[]
}

export const terminalApi = {
  capabilities: () => api.get<TerminalCapabilities>('/terminal/capabilities'),
  ticket: (data: { node_id: string; mode: 'low' | 'admin'; profile?: string }) =>
    api.post('/terminal/tickets', data),
  sessions: () => api.get('/terminal/sessions'),
  closeSession: (id: string) => api.delete(`/terminal/sessions/${id}`),
  brokers: () => api.get('/terminal/brokers'),
  rotateBrokerCredential: (nodeId: string) =>
    api.post(`/terminal/brokers/${nodeId}/credential`),
  revokeBrokerCredential: (nodeId: string) =>
    api.delete(`/terminal/brokers/${nodeId}/credential`),
}
