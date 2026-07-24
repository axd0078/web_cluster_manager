import { defineStore } from 'pinia'
import { ref } from 'vue'
import { monitorApi } from '../api/monitor'

export interface MetricSnapshot {
  node_id: string
  ip: string
  hostname: string | null
  cpu_percent: number | null
  mem_percent: number | null
  disk_percent: number | null
  time: string | null
}

export const useMonitorStore = defineStore('monitor', () => {
  const snapshots = ref<MetricSnapshot[]>([])
  const alerts = ref<any[]>([])
  const loading = ref(false)

  async function fetchCurrent() {
    loading.value = true
    try {
      const res = await monitorApi.current()
      snapshots.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function fetchAlerts(limit = 50) {
    const res = await monitorApi.alerts({ limit })
    alerts.value = res.data
  }

  function updateFromWs(data: any) {
    const idx = snapshots.value.findIndex(s => s.node_id === data.node_id)
    if (idx >= 0) {
      if (data.cpu_percent !== undefined) snapshots.value[idx].cpu_percent = data.cpu_percent
      if (data.mem_percent !== undefined) snapshots.value[idx].mem_percent = data.mem_percent
      if (data.disk_percent !== undefined) snapshots.value[idx].disk_percent = data.disk_percent
      snapshots.value[idx].time = data.time
    }
  }

  return { snapshots, alerts, loading, fetchCurrent, fetchAlerts, updateFromWs }
})
