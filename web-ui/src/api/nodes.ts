import client from './client'

export interface NodeInfo {
  id: string
  ip: string
  hostname: string
  os: string
  status: string
  version: string
  tags: Record<string, unknown>
  metadata_: Record<string, unknown>
  last_seen: string
  registered: string
}

export interface NodeListData {
  total: number
  online: number
  offline: number
  nodes: NodeInfo[]
}

export function listNodes(): Promise<{ data: NodeListData }> {
  return client.get('/nodes')
}

export function getNode(id: string): Promise<{ data: NodeInfo }> {
  return client.get(`/nodes/${id}`)
}
