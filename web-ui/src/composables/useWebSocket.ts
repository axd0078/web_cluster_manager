import { ref, onUnmounted } from 'vue'
import { useUserStore } from '../stores/user'

export interface WSMessage {
  type: string
  payload: Record<string, unknown>
  timestamp: string
}

export function useWebSocket() {
  const connected = ref(false)
  const lastMessage = ref<WSMessage | null>(null)
  const messageHandlers = new Map<string, Array<(msg: WSMessage) => void>>()

  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000

  function connect() {
    const userStore = useUserStore()
    if (!userStore.token) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const url = `${protocol}//${host}/api/v2/ws/ui?token=${userStore.token}`

    ws = new WebSocket(url)

    ws.onopen = () => {
      connected.value = true
      reconnectDelay = 1000
    }

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data)
        lastMessage.value = msg

        // Dispatch to handlers
        const handlers = messageHandlers.get(msg.type) || []
        handlers.forEach((fn) => fn(msg))

        // Also dispatch to wildcard handlers
        const wildcard = messageHandlers.get('*') || []
        wildcard.forEach((fn) => fn(msg))
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      connected.value = false
      scheduleReconnect()
    }

    ws.onerror = () => {
      ws?.close()
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    const delay = reconnectDelay
    reconnectDelay = Math.min(reconnectDelay * 2, 30000)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function on(type: string, handler: (msg: WSMessage) => void) {
    if (!messageHandlers.has(type)) {
      messageHandlers.set(type, [])
    }
    messageHandlers.get(type)!.push(handler)
  }

  function off(type: string, handler: (msg: WSMessage) => void) {
    const handlers = messageHandlers.get(type)
    if (handlers) {
      const idx = handlers.indexOf(handler)
      if (idx > -1) handlers.splice(idx, 1)
    }
  }

  function send(msg: Record<string, unknown>) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg))
    }
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    ws?.close()
    ws = null
    connected.value = false
  }

  onUnmounted(() => disconnect())

  return { connected, lastMessage, connect, disconnect, send, on, off }
}
