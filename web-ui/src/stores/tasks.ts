import { defineStore } from 'pinia'
import { ref } from 'vue'
import { tasksApi, type TaskCreatePayload, type TaskTargets } from '../api/tasks'

export interface Task {
  id: string
  type: string
  title: string | null
  status: string
  created_by: string | null
  created_by_name: string | null
  created: string | null
  started: string | null
  updated: string | null
  finished: string | null
  subtask_count: number
  completed_count: number
  progress: number
}

export interface Subtask {
  id: string
  node_id: string
  execution_id: string | null
  status: string
  attempts: number
  progress: number
  message: string | null
  error: string | null
  result: Record<string, unknown> | null
  started: string | null
  finished: string | null
}

export interface TaskDetail extends Task {
  params: Record<string, any>
  subtasks: Subtask[]
}

export interface ResolvedTargets {
  nodes: Array<{
    id: string
    hostname: string | null
    ip: string
    platform: string
    status: string
    compatible: boolean
    incompatibility: string | null
  }>
  common_profiles: Record<string, string[]>
}

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const detail = ref<TaskDetail | null>(null)
  const templates = ref<Array<{ type: string; title: string; params: Record<string, unknown> }>>([])
  const loading = ref(false)

  async function fetchTasks(params?: { status?: string; task_type?: string }) {
    loading.value = true
    try {
      const res = await tasksApi.list(params)
      tasks.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function fetchDetail(id: string) {
    const res = await tasksApi.get(id)
    detail.value = res.data
    const index = tasks.value.findIndex(task => task.id === id)
    if (index >= 0) tasks.value[index] = { ...tasks.value[index], ...res.data }
    return detail.value
  }

  async function resolveTargets(targets: TaskTargets): Promise<ResolvedTargets> {
    const res = await tasksApi.resolveTargets(targets)
    return res.data
  }

  async function createTask(data: TaskCreatePayload) {
    const res = await tasksApi.create(data)
    tasks.value.unshift(res.data)
    return res.data as Task
  }

  async function cancelTask(id: string) {
    const res = await tasksApi.cancel(id)
    await fetchDetail(id)
    return res.data.status as string
  }

  async function retryTask(id: string, targetNodeIds?: string[]) {
    await tasksApi.retry(id, targetNodeIds)
    await fetchDetail(id)
  }

  async function fetchTemplates() {
    const res = await tasksApi.templates()
    templates.value = res.data
  }

  return {
    tasks,
    detail,
    templates,
    loading,
    fetchTasks,
    fetchDetail,
    resolveTargets,
    createTask,
    cancelTask,
    retryTask,
    fetchTemplates,
  }
})
