import type { UpdateDeploymentTarget, UpdateResolvedNode } from '../../api/updates'

export const UPDATE_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  transferring: '传输中',
  staged: '已暂存',
  activating_canary: '激活 Canary',
  canary_running: 'Canary 发布中',
  awaiting_approval: '等待批准',
  activating: '分批激活',
  rolling_out: '分批发布中',
  health_checking: '健康检查',
  health_check: '健康检查',
  paused: '已暂停',
  cancel_requested: '取消确认中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
  rolling_back: '回滚中',
  rollback_running: '回滚中',
  rollback_failed: '回滚失败',
  rolled_back: '已回滚',
}

export type UpdateTagType = 'success' | 'warning' | 'danger' | 'info' | 'primary'

export function updateStatusLabel(status: string): string {
  return UPDATE_STATUS_LABELS[status] || status
}

export function updateStatusType(status: string): UpdateTagType {
  if (['completed', 'verified'].includes(status)) return 'success'
  if (['failed', 'rejected'].includes(status)) return 'danger'
  if (
    ['queued', 'awaiting_approval', 'paused', 'partial', 'cancel_requested'].includes(status)
  ) return 'warning'
  if (
    [
      'transferring', 'activating_canary', 'canary_running', 'activating',
      'rolling_out', 'health_checking', 'health_check', 'rolling_back', 'rollback_running',
    ].includes(status)
  ) return 'primary'
  return 'info'
}

export function resolvedNodeId(node: UpdateResolvedNode): string {
  return node.node_id || node.id || ''
}

export function incompatibilityReasons(node: UpdateResolvedNode): string[] {
  const reasons = node.incompatibility_reasons || node.reasons || []
  if (reasons.length) return reasons
  return node.reason ? [node.reason] : []
}

export function canRetryUpdateTarget(target: UpdateDeploymentTarget): boolean {
  return ['failed', 'paused'].includes(target.status)
}

export function canRollbackUpdateTarget(target: UpdateDeploymentTarget): boolean {
  return ['completed', 'healthy', 'activated'].includes(target.status)
    || ['completed', 'healthy'].includes(target.phase || '')
}
