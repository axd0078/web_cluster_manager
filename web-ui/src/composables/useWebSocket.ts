import { onUnmounted, ref, watch } from 'vue'
import { useNodeStore } from '../stores/nodes'
import { useUserStore } from '../stores/user'

export function useWebSocket() {
  const connected = ref(false)
  const lastMessage = ref<any>(null)
  const user = useUserStore()
  const nodes = useNodeStore()
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let disposed = false

  function connect() {
    if (disposed || !user.isAuthenticated || ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${protocol}//${location.host}/ws/frontend`)
    ws.onopen = () => { connected.value = true }
    ws.onmessage = event => {
      try {
        lastMessage.value = JSON.parse(event.data)
        if (['monitor_data', 'container_inventory'].includes(lastMessage.value?.type)) {
          nodes.applyRealtimeMessage(lastMessage.value)
        }
      } catch {
        lastMessage.value = event.data
      }
    }
    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!disposed && user.isAuthenticated) reconnectTimer = setTimeout(connect, 3000)
    }
    ws.onerror = () => ws?.close()
  }

  function disconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = null
    ws?.close()
    ws = null
    connected.value = false
  }

  function send(data: unknown) {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data))
  }

  watch(() => user.isAuthenticated, authenticated => {
    if (authenticated) connect()
    else disconnect()
  }, { immediate: true })

  onUnmounted(() => {
    disposed = true
    disconnect()
  })

  return { connected, lastMessage, send }
}
