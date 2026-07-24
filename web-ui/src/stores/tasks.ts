import { defineStore } from 'pinia'
import { ref } from 'vue'
import { tasksApi } from '../api/tasks'

export interface Task {
  id: string
  type: string
  title: string | null
  status: string
  created_by: string | null
  created: string | null
  finished: string | null
  subtask_count: number
  completed_count: number
}

export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref<Task[]>([])
  const templates = ref<any[]>([])
  const loading = ref(false)

  async function fetchTasks(params?: { status?: string; type?: string }) {
    loading.value = true
    try {
      const res = await tasksApi.list(params)
      tasks.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function createTask(data: {
    type: string; title?: string; params?: Record<string, unknown>; target_node_ids: string[]
  }) {
    const res = await tasksApi.create(data)
    tasks.value.unshift(res.data)
    return res.data
  }

  async function cancelTask(id: string) {
    await tasksApi.cancel(id)
    const t = tasks.value.find(t => t.id === id)
    if (t) t.status = 'cancelled'
  }

  async function retryTask(id: string) {
    await tasksApi.retry(id)
    const t = tasks.value.find(t => t.id === id)
    if (t) t.status = 'running'
  }

  async function fetchTemplates() {
    const res = await tasksApi.templates()
    templates.value = res.data
  }

  return { tasks, templates, loading, fetchTasks, createTask, cancelTask, retryTask, fetchTemplates }
})
