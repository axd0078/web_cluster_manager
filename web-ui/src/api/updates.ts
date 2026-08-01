import type { AxiosProgressEvent } from 'axios'
import api from './client'

export type UpdatePackageStatus = 'verified' | 'legacy_untrusted' | 'rejected' | string

export interface UpdatePackage {
  id: string
  version: string
  release_id: string | null
  description: string
  filename: string | null
  size: number
  sha256: string | null
  target_os: string | null
  target_arch: string | null
  python_abi: string | null
  key_id: string | null
  verification_status: UpdatePackageStatus
  created: string | null
}

export interface UpdateTargetResolutionRequest {
  package_id: string
  target_node_ids: string[]
  target_group_ids: string[]
  all_online: boolean
}

export interface UpdateResolvedNode {
  id?: string
  node_id?: string
  hostname?: string | null
  ip?: string | null
  status?: string
  version?: string | null
  compatible: boolean
  reason?: string | null
  reasons?: string[]
  incompatibility_reasons?: string[]
}

export interface UpdateTargetResolution {
  package_id: string
  nodes: UpdateResolvedNode[]
}

export interface UpdateDeploymentCreate extends UpdateTargetResolutionRequest {
  canary_node_ids: string[]
}

export type UpdateDeploymentStatus =
  | 'queued'
  | 'transferring'
  | 'activating_canary'
  | 'awaiting_approval'
  | 'activating'
  | 'paused'
  | 'cancel_requested'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | 'rolling_back'
  | 'rolled_back'
  | string

export interface UpdateDeploymentTarget {
  id?: string
  node_id: string
  status: string
  phase?: string
  progress: number
  attempts: number
  is_canary?: boolean
  from_version?: string | null
  to_version?: string | null
  message?: string | null
  error?: string | null
  rollback_status?: string | null
  started?: string | null
  updated?: string | null
  completed?: string | null
}

export interface UpdateDeployment {
  id: string
  package_id: string
  status: UpdateDeploymentStatus
  progress: number
  canary_node_ids: string[]
  created_by_name?: string | null
  created: string | null
  updated: string | null
  started?: string | null
  completed?: string | null
  error?: string | null
  targets: UpdateDeploymentTarget[]
}

export const updatesApi = {
  listPackages: () => api.get<UpdatePackage[]>('/updates/packages'),

  createPackage: (
    file: File,
    description = '',
    onUploadProgress?: (event: AxiosProgressEvent) => void,
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (description) form.append('description', description)
    return api.post<UpdatePackage>('/updates/packages', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress,
    })
  },

  deletePackage: (packageId: string) =>
    api.delete(`/updates/packages/${encodeURIComponent(packageId)}`),

  resolveTargets: (data: UpdateTargetResolutionRequest) =>
    api.post<UpdateTargetResolution>('/updates/targets/resolve', data),

  listDeployments: () =>
    api.get<UpdateDeployment[] | { items: UpdateDeployment[] }>('/updates/deployments'),

  createDeployment: (data: UpdateDeploymentCreate) =>
    api.post<UpdateDeployment>('/updates/deployments', data),

  getDeployment: (deploymentId: string) =>
    api.get<UpdateDeployment>(`/updates/deployments/${encodeURIComponent(deploymentId)}`),

  approveDeployment: (deploymentId: string) =>
    api.post<UpdateDeployment>(`/updates/deployments/${encodeURIComponent(deploymentId)}/approve`),

  cancelDeployment: (deploymentId: string) =>
    api.post<UpdateDeployment>(`/updates/deployments/${encodeURIComponent(deploymentId)}/cancel`),

  retryDeployment: (deploymentId: string, targetNodeIds?: string[]) =>
    api.post<UpdateDeployment>(
      `/updates/deployments/${encodeURIComponent(deploymentId)}/retry`,
      targetNodeIds?.length ? { target_node_ids: targetNodeIds } : {},
    ),

  rollbackDeployment: (deploymentId: string, targetNodeIds?: string[]) =>
    api.post<UpdateDeployment>(
      `/updates/deployments/${encodeURIComponent(deploymentId)}/rollback`,
      targetNodeIds?.length ? { target_node_ids: targetNodeIds } : {},
    ),
}
