import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../api/tasks', () => ({
  tasksApi: {
    list: vi.fn(),
    create: vi.fn(),
    resolveTargets: vi.fn(),
    get: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    templates: vi.fn(),
  },
}))

import { tasksApi } from '../api/tasks'
import { useTaskStore } from './tasks'

const task = {
  id: 'task-1',
  type: 'health_check',
  title: 'health',
  status: 'queued',
  created_by: 'user-1',
  created_by_name: 'operator',
  created: null,
  started: null,
  updated: null,
  finished: null,
  subtask_count: 1,
  completed_count: 0,
  progress: 0,
}

describe('task store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('passes typed filters to the list API', async () => {
    vi.mocked(tasksApi.list).mockResolvedValue({ data: [task] } as any)
    const store = useTaskStore()
    await store.fetchTasks({ status: 'paused', task_type: 'backup_files' })
    expect(tasksApi.list).toHaveBeenCalledWith({
      status: 'paused',
      task_type: 'backup_files',
    })
    expect(store.tasks).toEqual([task])
  })

  it('does not forge cancellation success and refreshes the server detail', async () => {
    vi.mocked(tasksApi.cancel).mockResolvedValue({
      data: { status: 'cancel_requested' },
    } as any)
    vi.mocked(tasksApi.get).mockResolvedValue({
      data: {
        ...task,
        status: 'cancel_requested',
        params: {},
        subtasks: [],
      },
    } as any)
    const store = useTaskStore()
    store.tasks = [{ ...task }]
    const status = await store.cancelTask(task.id)
    expect(status).toBe('cancel_requested')
    expect(store.tasks[0].status).toBe('cancel_requested')
  })

  it('sends only explicitly selected nodes to retry', async () => {
    vi.mocked(tasksApi.retry).mockResolvedValue({ data: {} } as any)
    vi.mocked(tasksApi.get).mockResolvedValue({
      data: { ...task, params: {}, subtasks: [] },
    } as any)
    const store = useTaskStore()
    await store.retryTask(task.id, ['node-2'])
    expect(tasksApi.retry).toHaveBeenCalledWith(task.id, ['node-2'])
  })
})
