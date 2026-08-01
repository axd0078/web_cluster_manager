<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWebSocket } from '../composables/useWebSocket'
import { nodesApi } from '../api/nodes'
import TaskStatusTag from '../components/tasks/TaskStatusTag.vue'
import { TASK_STATUS_LABELS } from '../components/tasks/taskPresentation'
import { useNodeStore } from '../stores/nodes'
import {
  useTaskStore,
  type ResolvedTargets,
  type Subtask,
  type Task,
} from '../stores/tasks'
import { useUserStore } from '../stores/user'

const taskStore = useTaskStore()
const nodeStore = useNodeStore()
const userStore = useUserStore()
const { lastMessage, connected } = useWebSocket()

const groups = ref<Array<{ id: string; name: string; node_count: number }>>([])
const createVisible = ref(false)
const detailVisible = ref(false)
const resolving = ref(false)
const submitting = ref(false)
const resolved = ref<ResolvedTargets | null>(null)
const selectedRetryNodes = ref<string[]>([])
const filters = reactive({ status: '', task_type: '' })
let refreshTimer: ReturnType<typeof setInterval> | null = null

const form = reactive({
  type: 'health_check',
  title: '',
  target_node_ids: [] as string[],
  target_group_ids: [] as string[],
  all_online: false,
  profile: '',
  older_than_days: 7,
  source: '.',
})

const typeLabels: Record<string, string> = {
  health_check: '健康检查',
  clean_logs: '日志清理',
  backup_files: '文件备份',
  restart_service: '服务重启',
  batch_command: '批量命令',
}

const statusLabels = TASK_STATUS_LABELS

const availableProfiles = computed(() => (
  resolved.value?.common_profiles[form.type] ?? []
))
const resolvedCompatible = computed(() => (
  !!resolved.value
  && resolved.value.nodes.length > 0
  && resolved.value.nodes.every(node => node.compatible)
))
const canCancel = computed(() => (
  !!taskStore.detail && ['queued', 'running', 'paused'].includes(taskStore.detail.status)
))
const canExecutePreview = computed(() => {
  const task = taskStore.detail
  return task?.type === 'clean_logs'
    && task.status === 'completed'
    && task.params.dry_run === true
})

watch(
  [
    () => [...form.target_node_ids],
    () => [...form.target_group_ids],
    () => form.all_online,
  ],
  () => {
    resolved.value = null
    form.profile = ''
  },
)

watch(() => form.type, () => {
  form.profile = ''
})

watch(lastMessage, message => {
  if (!['task_progress', 'task_result'].includes(message?.type)) return
  void refresh(false)
  const detailId = taskStore.detail?.id
  if (detailVisible.value && detailId && detailId === message?.payload?.task_id) {
    void taskStore.fetchDetail(detailId)
  }
})

watch(
  () => userStore.user?.permissions.join(','),
  () => taskStore.fetchTemplates(),
)

onMounted(async () => {
  const [groupResponse] = await Promise.all([
    nodesApi.listGroups(),
    nodeStore.fetchNodes(),
    taskStore.fetchTemplates(),
    taskStore.fetchTasks(),
  ])
  groups.value = groupResponse.data
  refreshTimer = setInterval(() => void refresh(false), 10_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

async function refresh(showLoading = true) {
  if (!showLoading && taskStore.loading) return
  await taskStore.fetchTasks({
    status: filters.status || undefined,
    task_type: filters.task_type || undefined,
  })
  if (detailVisible.value && taskStore.detail) {
    await taskStore.fetchDetail(taskStore.detail.id)
  }
}

function resetForm(type = taskStore.templates[0]?.type || 'health_check') {
  Object.assign(form, {
    type,
    title: '',
    target_node_ids: [],
    target_group_ids: [],
    all_online: false,
    profile: '',
    older_than_days: 7,
    source: '.',
  })
  resolved.value = null
}

function openCreate() {
  resetForm()
  createVisible.value = true
}

async function resolveTargetSet() {
  if (!form.target_node_ids.length && !form.target_group_ids.length && !form.all_online) {
    ElMessage.warning('请选择节点、分组或全部在线节点')
    return null
  }
  resolving.value = true
  try {
    resolved.value = await taskStore.resolveTargets({
      target_node_ids: form.target_node_ids,
      target_group_ids: form.target_group_ids,
      all_online: form.all_online,
    })
    if (!resolvedCompatible.value) {
      ElMessage.warning('解析结果包含不支持 task v2 的旧 Agent')
    } else {
      ElMessage.success(`已解析 ${resolved.value.nodes.length} 个 Agent`)
    }
    return resolved.value
  } finally {
    resolving.value = false
  }
}

function buildParams(): Record<string, unknown> {
  if (form.type === 'health_check') return {}
  if (form.type === 'clean_logs') {
    return {
      profile: form.profile,
      older_than_days: form.older_than_days,
      dry_run: true,
    }
  }
  if (form.type === 'backup_files') {
    return { profile: form.profile, source: form.source.trim() }
  }
  return { profile: form.profile }
}

async function submitTask() {
  const targetResolution = resolved.value || await resolveTargetSet()
  if (!targetResolution || !resolvedCompatible.value) return
  if (form.type !== 'health_check' && !form.profile) {
    ElMessage.warning('请选择所有目标共同支持的配置档')
    return
  }
  if (form.type === 'backup_files' && !form.source.trim()) {
    ElMessage.warning('请输入配置档根目录内的相对源路径')
    return
  }
  if (['restart_service', 'batch_command'].includes(form.type)) {
    await ElMessageBox.confirm(
      `即将在 ${targetResolution.nodes.length} 个 Agent 上执行${typeLabels[form.type]}，是否继续？`,
      '危险任务二次确认',
      { type: 'warning', confirmButtonText: '确认执行' },
    )
  }
  submitting.value = true
  try {
    await taskStore.createTask({
      type: form.type,
      title: form.title.trim() || typeLabels[form.type],
      params: buildParams(),
      target_node_ids: form.target_node_ids,
      target_group_ids: form.target_group_ids,
      all_online: form.all_online,
    })
    createVisible.value = false
    ElMessage.success(form.type === 'clean_logs' ? '日志清理预览已创建' : '任务已创建')
    await refresh()
  } finally {
    submitting.value = false
  }
}

async function showDetail(task: Task) {
  await taskStore.fetchDetail(task.id)
  selectedRetryNodes.value = []
  detailVisible.value = true
}

async function cancelCurrent() {
  const task = taskStore.detail
  if (!task) return
  await ElMessageBox.confirm(
    '只有 Agent 确认后节点任务才会标记为已取消；确认超时将保持“取消确认中”。',
    '取消任务',
    { type: 'warning' },
  )
  const status = await taskStore.cancelTask(task.id)
  ElMessage.success(status === 'cancel_requested' ? '取消请求已发送，等待 Agent 确认' : '任务已取消')
  await refresh()
}

function retrySelectable(row: Subtask) {
  return ['failed', 'paused'].includes(row.status)
}

function handleRetrySelection(rows: Subtask[]) {
  selectedRetryNodes.value = rows
    .filter(retrySelectable)
    .map(row => row.node_id)
}

async function retrySelected() {
  const task = taskStore.detail
  if (!task) return
  if (!selectedRetryNodes.value.length) {
    ElMessage.warning('请勾选 failed 或 paused 节点')
    return
  }
  await taskStore.retryTask(task.id, selectedRetryNodes.value)
  selectedRetryNodes.value = []
  ElMessage.success('所选节点已使用新的 execution_id 重试')
  await refresh()
}

async function executePreview() {
  const preview = taskStore.detail
  if (!preview) return
  await ElMessageBox.confirm(
    `将按预览结果在 ${preview.subtasks.length} 个 Agent 上正式删除匹配的过期日志。预览仅在完成后 30 分钟内有效。`,
    '正式清理二次确认',
    { type: 'warning', confirmButtonText: '确认清理' },
  )
  const task = await taskStore.createTask({
    type: 'clean_logs',
    title: `${preview.title || '日志清理'}（正式执行）`,
    params: {
      profile: preview.params.profile,
      older_than_days: preview.params.older_than_days,
      dry_run: false,
      preview_task_id: preview.id,
    },
    target_node_ids: preview.subtasks.map(item => item.node_id),
    target_group_ids: [],
    all_online: false,
  })
  ElMessage.success('正式日志清理已创建')
  detailVisible.value = false
  await refresh()
  await showDetail(task)
}

function nodeLabel(nodeId: string) {
  const node = nodeStore.nodes.find(item => item.id === nodeId)
  return node ? `${node.hostname || node.ip} (${node.ip})` : nodeId
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function formatResult(value: Record<string, unknown> | null) {
  return value ? JSON.stringify(value, null, 2) : '—'
}
</script>

<template>
  <div class="task-center">
    <div class="page-header">
      <div>
        <h2>任务中心</h2>
        <p>五类任务均由 Agent 本地配置档约束；Docker 容器不会成为任务目标。</p>
      </div>
      <el-space>
        <el-tag :type="connected ? 'success' : 'warning'">
          {{ connected ? '实时更新已连接' : '实时连接断开，10 秒轮询兜底' }}
        </el-tag>
        <el-button type="primary" @click="openCreate">创建任务</el-button>
      </el-space>
    </div>

    <el-card>
      <div class="filters">
        <el-select v-model="filters.status" clearable placeholder="全部状态" @change="refresh()">
          <el-option
            v-for="(label, value) in statusLabels"
            :key="value"
            :label="label"
            :value="value"
          />
        </el-select>
        <el-select v-model="filters.task_type" clearable placeholder="全部类型" @change="refresh()">
          <el-option
            v-for="template in taskStore.templates"
            :key="template.type"
            :label="template.title"
            :value="template.type"
          />
        </el-select>
        <el-button @click="refresh()">刷新</el-button>
      </div>

      <el-table v-loading="taskStore.loading" :data="taskStore.tasks" stripe>
        <el-table-column label="任务" min-width="220">
          <template #default="{ row }">
            <div class="task-title">{{ row.title || typeLabels[row.type] }}</div>
            <span class="muted">{{ typeLabels[row.type] || row.type }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <TaskStatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="总体进度" min-width="180">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :stroke-width="10" />
            <span class="muted">{{ row.completed_count }} / {{ row.subtask_count }} 节点完成</span>
          </template>
        </el-table-column>
        <el-table-column v-if="userStore.isAdmin" prop="created_by_name" label="创建者" width="130" />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="showDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!taskStore.loading && !taskStore.tasks.length" description="暂无任务记录" />
    </el-card>

    <el-dialog v-model="createVisible" title="创建 Agent 任务" width="720px">
      <el-form label-width="120px">
        <el-form-item label="任务类型" required>
          <el-select v-model="form.type" style="width: 100%">
            <el-option
              v-for="template in taskStore.templates"
              :key="template.type"
              :label="template.title"
              :value="template.type"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标题">
          <el-input v-model="form.title" maxlength="255" show-word-limit />
        </el-form-item>
        <el-divider content-position="left">目标（服务端展开、去重，最多 32 个 Agent）</el-divider>
        <el-form-item label="节点">
          <el-select v-model="form.target_node_ids" multiple filterable collapse-tags style="width: 100%">
            <el-option
              v-for="node in nodeStore.nodes"
              :key="node.id"
              :label="`${node.hostname || node.ip} · ${node.status} · ${node.platform}`"
              :value="node.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="分组">
          <el-select v-model="form.target_group_ids" multiple filterable collapse-tags style="width: 100%">
            <el-option
              v-for="group in groups"
              :key="group.id"
              :label="`${group.name} (${group.node_count})`"
              :value="group.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="在线节点">
          <el-checkbox v-model="form.all_online">包含全部在线 Agent</el-checkbox>
          <el-button :loading="resolving" style="margin-left: 16px" @click="resolveTargetSet">
            解析目标与共同配置档
          </el-button>
        </el-form-item>

        <el-alert
          v-if="resolved"
          :type="resolvedCompatible ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="resolution"
        >
          <template #title>
            已解析 {{ resolved.nodes.length }} 个 Agent；
            {{ resolvedCompatible ? '全部支持 task v2' : '存在协议不兼容节点，不能创建任务' }}
          </template>
        </el-alert>

        <template v-if="form.type !== 'health_check'">
          <el-form-item label="共同配置档" required>
            <el-select
              v-model="form.profile"
              :disabled="!resolvedCompatible"
              placeholder="请先解析目标"
              style="width: 100%"
            >
              <el-option
                v-for="profile in availableProfiles"
                :key="profile"
                :label="profile"
                :value="profile"
              />
            </el-select>
          </el-form-item>
        </template>
        <template v-if="form.type === 'clean_logs'">
          <el-form-item label="超过天数">
            <el-input-number v-model="form.older_than_days" :min="1" :max="3650" />
          </el-form-item>
          <el-alert
            type="info"
            :closable="false"
            title="第一次只创建 dry-run 预览；预览成功后从详情页发起正式清理。"
          />
        </template>
        <el-form-item v-if="form.type === 'backup_files'" label="相对源路径" required>
          <el-input v-model="form.source" placeholder="例如 data 或 ." />
          <span class="muted">ZIP 仅保存在 Agent 的 agent_data/task_backups。</span>
        </el-form-item>
        <el-alert
          v-if="['restart_service', 'batch_command'].includes(form.type)"
          type="warning"
          :closable="false"
          title="危险任务仅 admin 可执行，提交前还会二次确认。"
        />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitTask">创建任务</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="任务详情" size="82%">
      <template v-if="taskStore.detail">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="标题">{{ taskStore.detail.title }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ typeLabels[taskStore.detail.type] }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <TaskStatusTag :status="taskStore.detail.status" />
          </el-descriptions-item>
          <el-descriptions-item label="创建者">{{ taskStore.detail.created_by_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(taskStore.detail.created) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDate(taskStore.detail.updated) }}</el-descriptions-item>
        </el-descriptions>

        <div class="detail-actions">
          <el-button v-if="canExecutePreview" type="danger" @click="executePreview">
            按此预览正式清理
          </el-button>
          <el-button v-if="canCancel" type="warning" @click="cancelCurrent">取消任务</el-button>
          <el-button
            type="primary"
            :disabled="!selectedRetryNodes.length"
            @click="retrySelected"
          >
            重试所选失败/暂停节点
          </el-button>
        </div>

        <el-table
          :data="taskStore.detail.subtasks"
          border
          @selection-change="handleRetrySelection"
        >
          <el-table-column type="selection" width="48" :selectable="retrySelectable" />
          <el-table-column label="Agent" min-width="190">
            <template #default="{ row }">{{ nodeLabel(row.node_id) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="125">
            <template #default="{ row }">
              <TaskStatusTag :status="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="进度" width="150">
            <template #default="{ row }"><el-progress :percentage="row.progress" /></template>
          </el-table-column>
          <el-table-column prop="attempts" label="尝试" width="75" />
          <el-table-column prop="message" label="消息" min-width="170" show-overflow-tooltip />
          <el-table-column prop="error" label="错误" min-width="190" show-overflow-tooltip />
          <el-table-column label="开始/结束" width="210">
            <template #default="{ row }">
              <div>{{ formatDate(row.started) }}</div>
              <div class="muted">{{ formatDate(row.finished) }}</div>
            </template>
          </el-table-column>
          <el-table-column type="expand" width="55">
            <template #default="{ row }">
              <pre class="result">{{ formatResult(row.result) }}</pre>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.task-center { display: grid; gap: 16px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: var(--el-text-color-secondary); }
.filters { display: flex; gap: 12px; margin-bottom: 16px; }
.filters .el-select { width: 180px; }
.task-title { font-weight: 600; margin-bottom: 3px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.resolution { margin-bottom: 18px; }
.detail-actions { display: flex; justify-content: flex-end; gap: 10px; margin: 18px 0; }
.result {
  margin: 0;
  padding: 12px;
  max-height: 360px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--el-fill-color-light);
  border-radius: 4px;
}
</style>
