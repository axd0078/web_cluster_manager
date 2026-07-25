export const TASK_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  paused: '已暂停',
  cancel_requested: '取消确认中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
}

export const TASK_STATUS_TYPES: Record<
  string,
  'success' | 'warning' | 'info' | 'danger' | 'primary'
> = {
  queued: 'info',
  running: 'primary',
  paused: 'warning',
  cancel_requested: 'warning',
  completed: 'success',
  partial: 'warning',
  failed: 'danger',
  cancelled: 'info',
}
