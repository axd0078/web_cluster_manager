import api from './client'

export interface EnrollmentTokenInfo {
  id: string
  label: string | null
  created: string
  expires_at: string
  used_at: string | null
  revoked_at: string | null
}

export const agentsApi = {
  createEnrollmentToken: (label: string, ttlMinutes: number) =>
    api.post('/agent-enrollment-tokens', { label: label || undefined, ttl_minutes: ttlMinutes }),
  listEnrollmentTokens: () => api.get<EnrollmentTokenInfo[]>('/agent-enrollment-tokens'),
  revokeEnrollmentToken: (id: string) => api.delete(`/agent-enrollment-tokens/${id}`),
}
