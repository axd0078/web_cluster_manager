<template>
  <div class="dashboard">
    <h2>仪表盘</h2>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="stat-card">
            <div class="stat-value online">{{ nodeList.online }}</div>
            <div class="stat-label">在线节点</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="stat-card">
            <div class="stat-value offline">{{ nodeList.offline }}</div>
            <div class="stat-label">离线节点</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="stat-card">
            <div class="stat-value total">{{ nodeList.total }}</div>
            <div class="stat-label">总节点数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <div class="stat-card">
            <div class="stat-value ws">
              <el-tag :type="wsConnected ? 'success' : 'danger'" size="large">
                {{ wsConnected ? '已连接' : '断开' }}
              </el-tag>
            </div>
            <div class="stat-label">WebSocket</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Agent 在线变化日志 -->
    <el-card class="event-card" header="实时事件">
      <el-timeline v-if="events.length > 0">
        <el-timeline-item
          v-for="(e, i) in events.slice(-10).reverse()"
          :key="i"
          :type="e.type === 'online' ? 'success' : 'danger'"
          :timestamp="e.time"
        >
          {{ e.text }}
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无事件" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { listNodes } from '../api/nodes'
import type { NodeListData } from '../api/nodes'
import type { WSMessage } from '../composables/useWebSocket'

const props = defineProps<{
  wsConnected: boolean
  wsSend: (msg: Record<string, unknown>) => void
  wsOn: (type: string, handler: (msg: WSMessage) => void) => void
  wsOff: (type: string, handler: (msg: WSMessage) => void) => void
}>()

const nodeList = reactive<NodeListData>({ total: 0, online: 0, offline: 0, nodes: [] })
const events = ref<Array<{ type: string; text: string; time: string }>>([])

function addEvent(type: string, text: string) {
  events.value.push({ type, text, time: new Date().toLocaleTimeString() })
  if (events.value.length > 100) events.value.shift()
}

function handleNodeOnline(msg: WSMessage) {
  const id = String(msg.payload?.node_id || '?')
  addEvent('online', `节点上线: ${id}`)
  fetchNodes()
}

function handleNodeOffline(msg: WSMessage) {
  const id = String(msg.payload?.node_id || '?')
  addEvent('offline', `节点离线: ${id}`)
  fetchNodes()
}

function handleNodeRegistered(msg: WSMessage) {
  const id = String(msg.payload?.node_id || '?')
  addEvent('online', `节点注册: ${id}`)
  fetchNodes()
}

function handleHeartbeat(_msg: WSMessage) {
  // Update last seen but don't flood the timeline
}

async function fetchNodes() {
  try {
    const res = await listNodes()
    Object.assign(nodeList, res.data)
  } catch {
    // ignore
  }
}

onMounted(() => {
  fetchNodes()
  props.wsOn('node_online', handleNodeOnline)
  props.wsOn('node_offline', handleNodeOffline)
  props.wsOn('node_registered', handleNodeRegistered)
  props.wsOn('heartbeat', handleHeartbeat)
})

watch(
  () => props.wsConnected,
  (val) => {
    if (val) {
      props.wsOn('node_online', handleNodeOnline)
      props.wsOn('node_offline', handleNodeOffline)
      props.wsOn('node_registered', handleNodeRegistered)
      props.wsOn('heartbeat', handleHeartbeat)
    } else {
      props.wsOff('node_online', handleNodeOnline)
      props.wsOff('node_offline', handleNodeOffline)
      props.wsOff('node_registered', handleNodeRegistered)
      props.wsOff('heartbeat', handleHeartbeat)
    }
  }
)
</script>

<style scoped>
.dashboard h2 { margin-bottom: 16px; }
.stat-row { margin-bottom: 16px; }
.stat-card { text-align: center; padding: 8px 0; }
.stat-value { font-size: 32px; font-weight: 700; }
.stat-value.online { color: #67c23a; }
.stat-value.offline { color: #f56c6c; }
.stat-value.total { color: #409eff; }
.stat-value.ws { font-size: 14px; }
.stat-label { color: #909399; margin-top: 4px; font-size: 14px; }
.event-card { margin-top: 0; }
</style>
