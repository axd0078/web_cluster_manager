<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useNodeStore, type Container, type Node } from '../stores/nodes'
import { useUserStore } from '../stores/user'
import { nodesApi } from '../api/nodes'

type ContainerRow = Container & { kind: 'container'; children?: never }
type HostRow = Node & { kind: 'host'; children: ContainerRow[] }
type TreeRow = HostRow | ContainerRow

const nodeStore = useNodeStore()
const userStore = useUserStore()
const search = ref('')
const statusFilter = ref('')
const selectedNodes = ref<TreeRow[]>([])
const logsVisible = ref(false)
const logsLoading = ref(false)
const logsTitle = ref('')
const logsContent = ref('')

const rows = computed<HostRow[]>(() => nodeStore.nodes.map(node => ({
  ...node,
  kind: 'host' as const,
  children: node.containers.map(container => ({ ...container, kind: 'container' as const })),
})))

const filtered = computed(() => {
  const query = search.value.trim().toLowerCase()
  return rows.value.filter(row => {
    const statusMatch = !statusFilter.value || row.status === statusFilter.value
    const searchMatch = !query || row.ip.toLowerCase().includes(query)
      || row.hostname?.toLowerCase().includes(query)
      || row.children.some(container => container.name.toLowerCase().includes(query) || container.image?.toLowerCase().includes(query))
    return statusMatch && searchMatch
  })
})

onMounted(() => nodeStore.fetchNodes())

function onSelectionChange(value: TreeRow[]) {
  selectedNodes.value = value
}

async function setStatus(nodeId: string, status: string) {
  await nodesApi.updateStatus(nodeId, status)
  await nodeStore.fetchNodes()
}

async function deleteNode(nodeId: string) {
  await ElMessageBox.confirm('确定要移除该宿主节点吗？关联容器记录也会删除。', '确认', { type: 'warning' })
  await nodesApi.delete(nodeId)
  await nodeStore.fetchNodes()
}

async function containerAction(container: Container, action: 'start' | 'stop' | 'restart') {
  await ElMessageBox.confirm(`确认对容器 ${container.name} 执行 ${action}？`, 'Docker 操作确认', { type: 'warning' })
  await nodesApi.containerAction(container.id, action)
  ElMessage.success('操作已由宿主 Agent 执行')
  await nodeStore.fetchNodes()
}

async function showLogs(container: Container) {
  logsVisible.value = true
  logsLoading.value = true
  logsTitle.value = `${container.name} 日志（最近 200 行）`
  logsContent.value = ''
  try {
    const response = await nodesApi.containerLogs(container.id)
    logsContent.value = response.data.logs || '(无日志)'
  } finally {
    logsLoading.value = false
  }
}

function getStatusType(status: string) {
  const map: Record<string, string> = { online: 'success', running: 'success', offline: 'danger', exited: 'info', removed: 'info', maintenance: 'warning' }
  return map[status] || 'info'
}
</script>

<template>
  <div>
    <h2>节点与 Docker 容器</h2>
    <el-row :gutter="12" style="margin-bottom: 16px">
      <el-col :span="7"><el-input v-model="search" placeholder="搜索 IP、主机、容器或镜像" clearable /></el-col>
      <el-col :span="4">
        <el-select v-model="statusFilter" placeholder="宿主状态" clearable>
          <el-option label="在线" value="online" /><el-option label="离线" value="offline" /><el-option label="维护中" value="maintenance" />
        </el-select>
      </el-col>
      <el-col :span="4" :offset="9" style="text-align:right"><el-button type="primary" @click="nodeStore.fetchNodes()">刷新</el-button></el-col>
    </el-row>

    <el-table
      :data="filtered" row-key="id" :tree-props="{ children: 'children' }"
      v-loading="nodeStore.loading" @selection-change="onSelectionChange" stripe
    >
      <el-table-column type="selection" width="42" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }"><el-tag size="small" :type="row.kind === 'host' ? 'primary' : 'info'">{{ row.kind === 'host' ? '宿主' : '容器' }}</el-tag></template>
      </el-table-column>
      <el-table-column label="名称 / IP" min-width="190">
        <template #default="{ row }">{{ row.kind === 'host' ? `${row.hostname || '未知'} (${row.ip})` : row.name }}</template>
      </el-table-column>
      <el-table-column label="系统 / 镜像" min-width="210">
        <template #default="{ row }">{{ row.kind === 'host' ? row.os : row.image }}</template>
      </el-table-column>
      <el-table-column label="资源" width="180">
        <template #default="{ row }">
          <span v-if="row.kind === 'container'">CPU {{ row.cpu_percent ?? '—' }}% / MEM {{ row.mem_percent ?? '—' }}%</span>
          <span v-else>Agent {{ row.version || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }"><el-tag :type="getStatusType(row.kind === 'host' ? row.status : row.state)" size="small">{{ row.kind === 'host' ? row.status : row.state }}</el-tag></template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <template v-if="row.kind === 'host'">
            <el-button size="small" @click="setStatus(row.id, 'maintenance')">维护</el-button>
            <el-button v-if="userStore.isAdmin" size="small" type="danger" @click="deleteNode(row.id)">移除</el-button>
          </template>
          <template v-else>
            <el-button v-if="userStore.isAdmin" size="small" @click="showLogs(row)">日志</el-button>
            <el-button v-if="userStore.isAdmin" size="small" type="success" @click="containerAction(row, 'start')">启动</el-button>
            <el-button v-if="userStore.isAdmin" size="small" type="warning" @click="containerAction(row, 'restart')">重启</el-button>
            <el-button v-if="userStore.isAdmin" size="small" type="danger" @click="containerAction(row, 'stop')">停止</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="logsVisible" :title="logsTitle" width="75%">
      <el-skeleton v-if="logsLoading" :rows="8" animated />
      <pre v-else class="logs">{{ logsContent }}</pre>
    </el-dialog>
  </div>
</template>

<style scoped>
.logs { max-height: 60vh; overflow: auto; padding: 12px; color: #d6deeb; background: #111827; white-space: pre-wrap; word-break: break-all; }
</style>
