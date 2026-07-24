import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { nodesApi } from '../api/nodes'

export interface Node {
  id: string
  ip: string
  hostname: string | null
  os: string | null
  platform: string
  credential_state: string
  status: string
  version: string | null
  last_seen: string | null
  registered: string | null
  cpu_percent?: number | null
  mem_percent?: number | null
  disk_percent?: number | null
  containers: Container[]
}

export interface Container {
  id: string
  host_node_id: string
  runtime_id: string
  name: string
  image: string | null
  state: string
  status: string | null
  cpu_percent: number | null
  mem_percent: number | null
  mem_usage: string | null
  last_seen: string | null
}

export interface ClusterHealth {
  total_nodes: number
  online_nodes: number
  offline_nodes: number
  maintenance_nodes: number
  avg_cpu: number | null
  avg_memory: number | null
  avg_disk: number | null
  alerts_active: number
  health_score: number
}

export const useNodeStore = defineStore('nodes', () => {
  const nodes = ref<Node[]>([])
  const health = ref<ClusterHealth | null>(null)
  const loading = ref(false)

  const onlineNodes = computed(() => nodes.value.filter(n => n.status === 'online'))
  const offlineNodes = computed(() => nodes.value.filter(n => n.status === 'offline'))

  async function fetchNodes() {
    loading.value = true
    try {
      const res = await nodesApi.list()
      nodes.value = res.data
    } finally {
      loading.value = false
    }
  }

  async function fetchHealth() {
    const res = await nodesApi.health()
    health.value = res.data
  }

  function applyRealtimeMessage(message: any) {
    const data = message?.payload || {}
    if (message?.type === 'monitor_data') {
      const node = nodes.value.find(item => item.id === data.node_id)
      if (node) {
        node.cpu_percent = data.cpu_percent
        node.mem_percent = data.mem_percent
        node.disk_percent = data.disk_percent
      }
    } else if (message?.type === 'container_inventory') {
      void fetchNodes()
    }
  }

  return { nodes, health, loading, onlineNodes, offlineNodes, fetchNodes, fetchHealth, applyRealtimeMessage }
})
