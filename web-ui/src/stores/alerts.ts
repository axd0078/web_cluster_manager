import { defineStore } from 'pinia'
import { ref } from 'vue'
import { monitorApi } from '../api/monitor'

export interface Alert {
  id: string
  node_id: string | null
  severity: string
  message: string
  resolved: boolean
  created: string | null
}

export const useAlertStore = defineStore('alerts', () => {
  const alerts = ref<Alert[]>([])
  const rules = ref<any[]>([])
  const unreadCount = ref(0)

  async function fetchAlerts(params?: { limit?: number; severity?: string; resolved?: boolean }) {
    const res = await monitorApi.listAlerts(params)
    alerts.value = res.data
    unreadCount.value = alerts.value.filter(a => !a.resolved).length
  }

  async function resolveAlert(alertId: string) {
    await monitorApi.resolveAlert(alertId)
    const a = alerts.value.find(a => a.id === alertId)
    if (a) {
      a.resolved = true
      unreadCount.value = alerts.value.filter(x => !x.resolved).length
    }
  }

  async function fetchRules() {
    const res = await monitorApi.listRules()
    rules.value = res.data
  }

  function addFromWs(alert: Alert) {
    alerts.value.unshift(alert)
    if (!alert.resolved) unreadCount.value++
  }

  return { alerts, rules, unreadCount, fetchAlerts, resolveAlert, fetchRules, addFromWs }
})
