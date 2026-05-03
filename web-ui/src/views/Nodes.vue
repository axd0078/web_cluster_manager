<template>
  <div class="nodes-page">
    <h2>节点管理</h2>

    <!-- 工具栏 -->
    <div class="toolbar">
      <el-input v-model="search" placeholder="搜索 IP 或主机名..." style="width: 240px" clearable :prefix-icon="Search" />
      <el-select v-model="statusFilter" placeholder="状态筛选" style="width: 140px" clearable>
        <el-option label="全部" value="" />
        <el-option label="在线" value="online" />
        <el-option label="离线" value="offline" />
      </el-select>
      <el-button @click="fetchNodes" :icon="Refresh">刷新</el-button>
    </div>

    <!-- 表格 -->
    <el-table :data="filteredNodes" stripe border style="width: 100%" v-loading="loading">
      <el-table-column prop="ip" label="IP 地址" width="160" />
      <el-table-column prop="hostname" label="主机名" min-width="150" />
      <el-table-column prop="os" label="系统" width="100" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'online' ? 'success' : 'danger'" size="small" effect="dark">
            {{ row.status === 'online' ? '在线' : '离线' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="version" label="版本" width="100" />
      <el-table-column prop="registered" label="注册时间" width="180">
        <template #default="{ row }">
          {{ formatTime(row.registered) }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button size="small" :disabled="row.status !== 'online'" @click="handleCommand(row, 'get_system_info')">
            系统信息
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 命令结果 -->
    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="600px">
      <pre class="result-pre">{{ dialogContent }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { Search, Refresh } from '@element-plus/icons-vue'
import { listNodes } from '../api/nodes'
import type { NodeInfo, NodeListData } from '../api/nodes'
import type { WSMessage } from '../composables/useWebSocket'

const props = defineProps<{
  wsConnected: boolean
  wsSend: (msg: Record<string, unknown>) => void
  wsOn: (type: string, handler: (msg: WSMessage) => void) => void
  wsOff: (type: string, handler: (msg: WSMessage) => void) => void
}>()

const nodeList = reactive<NodeListData>({ total: 0, online: 0, offline: 0, nodes: [] })
const loading = ref(false)
const search = ref('')
const statusFilter = ref('')

const dialogVisible = ref(false)
const dialogTitle = ref('')
const dialogContent = ref('')

const filteredNodes = computed(() => {
  let nodes = nodeList.nodes
  if (search.value) {
    const q = search.value.toLowerCase()
    nodes = nodes.filter((n) => n.ip.toLowerCase().includes(q) || n.hostname.toLowerCase().includes(q))
  }
  if (statusFilter.value) {
    nodes = nodes.filter((n) => n.status === statusFilter.value)
  }
  return nodes
})

function formatTime(ts: string) {
  try { return new Date(ts).toLocaleString() } catch { return ts }
}

async function fetchNodes() {
  loading.value = true
  try {
    const res = await listNodes()
    Object.assign(nodeList, res.data)
  } catch {
    // ignore
  } finally {
    loading.value = false
  }
}

function handleCommand(node: NodeInfo, command: string) {
  if (!props.wsConnected) {
    dialogTitle.value = '错误'
    dialogContent.value = 'WebSocket 未连接'
    dialogVisible.value = true
    return
  }

  const requestId = Date.now().toString()
  dialogTitle.value = `执行中: ${command} → ${node.ip}`
  dialogContent.value = '等待结果...'
  dialogVisible.value = true

  // 发送命令
  props.wsSend({
    type: 'command',
    target: node.ip,
    command,
    params: {},
    request_id: requestId,
  })

  // 监听结果
  const handler = (msg: WSMessage) => {
    if (msg.payload?.request_id === requestId) {
      props.wsOff('command_result', handler)
      dialogTitle.value = `结果: ${command} → ${node.ip}`
      dialogContent.value = JSON.stringify(msg.payload?.result || msg.payload, null, 2)
    }
  }
  props.wsOn('command_result', handler)

  // 30 秒超时
  setTimeout(() => {
    props.wsOff('command_result', handler)
    if (dialogContent.value === '等待结果...') {
      dialogContent.value = '请求超时'
    }
  }, 30000)
}

onMounted(fetchNodes)
watch(() => props.wsConnected, fetchNodes)
</script>

<style scoped>
.nodes-page h2 { margin-bottom: 16px; }
.toolbar { display: flex; gap: 12px; margin-bottom: 16px; }
.result-pre {
  background: #f5f7fa; border: 1px solid #e4e7ed;
  padding: 12px; border-radius: 4px; max-height: 400px;
  overflow: auto; white-space: pre-wrap; font-size: 13px;
}
</style>
