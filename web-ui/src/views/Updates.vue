<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, type UploadFile, type UploadUserFile } from 'element-plus'
import { nodesApi } from '../api/nodes'
import {
  updatesApi,
  type UpdateDeployment,
  type UpdateDeploymentTarget,
  type UpdatePackage,
  type UpdateResolvedNode,
  type UpdateTargetResolution,
} from '../api/updates'
import {
  canRetryUpdateTarget,
  canRollbackUpdateTarget,
  incompatibilityReasons,
  resolvedNodeId,
  updateStatusLabel,
  updateStatusType,
} from '../components/updates/updatePresentation'
import { useWebSocket } from '../composables/useWebSocket'
import { useNodeStore } from '../stores/nodes'
import { useUserStore } from '../stores/user'

const MAX_PACKAGE_BYTES = 100 * 1024 * 1024

const userStore = useUserStore()
const nodeStore = useNodeStore()
const { connected, lastMessage } = useWebSocket()

const packages = ref<UpdatePackage[]>([])
const deployments = ref<UpdateDeployment[]>([])
const groups = ref<Array<{ id: string; name: string; node_count?: number }>>([])
const packagesLoading = ref(false)
const deploymentsLoading = ref(false)
const uploadVisible = ref(false)
const uploadFile = ref<File | null>(null)
const uploadFiles = ref<UploadUserFile[]>([])
const uploadDescription = ref('')
const uploadProgress = ref(0)
const uploading = ref(false)
const createVisible = ref(false)
const resolving = ref(false)
const creating = ref(false)
const resolved = ref<UpdateTargetResolution | null>(null)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<UpdateDeployment | null>(null)
const selectedDetailTargets = ref<UpdateDeploymentTarget[]>([])
let refreshTimer: ReturnType<typeof setInterval> | null = null

const deploymentForm = reactive({
  package_id: '',
  target_node_ids: [] as string[],
  target_group_ids: [] as string[],
  all_online: false,
  canary_node_ids: [] as string[],
})

const packageMap = computed(() => new Map(packages.value.map(item => [item.id, item])))
const verifiedPackages = computed(() => (
  packages.value.filter(item => item.verification_status === 'verified')
))
const canManage = computed(() => userStore.hasPermission('updates.manage'))
const resolvedNodes = computed(() => resolved.value?.nodes || [])
const allTargetsCompatible = computed(() => (
  resolvedNodes.value.length > 0 && resolvedNodes.value.every(node => node.compatible)
))
const compatibleResolvedNodes = computed(() => (
  resolvedNodes.value.filter(node => node.compatible && resolvedNodeId(node))
))
const selectedRetryNodeIds = computed(() => (
  selectedDetailTargets.value.filter(canRetryUpdateTarget).map(item => item.node_id)
))
const selectedRollbackNodeIds = computed(() => (
  selectedDetailTargets.value.filter(canRollbackUpdateTarget).map(item => item.node_id)
))

watch(
  [
    () => deploymentForm.package_id,
    () => [...deploymentForm.target_node_ids],
    () => [...deploymentForm.target_group_ids],
    () => deploymentForm.all_online,
  ],
  () => {
    resolved.value = null
    deploymentForm.canary_node_ids = []
  },
)

watch(lastMessage, message => {
  if (message?.type !== 'update_deployment_progress') return
  void loadDeployments(false)
  const deploymentId = message?.payload?.deployment_id
  if (detailVisible.value && detail.value && (!deploymentId || detail.value.id === deploymentId)) {
    void loadDetail(detail.value.id, false)
  }
})

onMounted(async () => {
  await Promise.all([
    loadPackages(),
    loadDeployments(),
    loadNodesAndGroups(),
  ])
  refreshTimer = setInterval(() => {
    void loadDeployments(false)
    if (detailVisible.value && detail.value) void loadDetail(detail.value.id, false)
  }, 8_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

async function loadNodesAndGroups() {
  const [, groupResponse] = await Promise.all([
    nodeStore.fetchNodes(),
    nodesApi.listGroups(),
  ])
  groups.value = groupResponse.data
}

async function loadPackages(showLoading = true) {
  if (showLoading) packagesLoading.value = true
  try {
    const response = await updatesApi.listPackages()
    packages.value = response.data
  } finally {
    if (showLoading) packagesLoading.value = false
  }
}

async function loadDeployments(showLoading = true) {
  if (showLoading) deploymentsLoading.value = true
  try {
    const response = await updatesApi.listDeployments()
    deployments.value = Array.isArray(response.data) ? response.data : response.data.items
  } finally {
    if (showLoading) deploymentsLoading.value = false
  }
}

async function loadDetail(deploymentId: string, showLoading = true) {
  if (showLoading) detailLoading.value = true
  try {
    detail.value = (await updatesApi.getDeployment(deploymentId)).data
  } finally {
    if (showLoading) detailLoading.value = false
  }
}

async function ensureStepUp(): Promise<boolean> {
  if (!userStore.isAdmin) {
    ElMessage.error('只有管理员可以修改更新中心')
    return false
  }
  if (canManage.value) return true
  try {
    const result = await ElMessageBox.prompt(
      '请输入当前管理员密码。高权限授权绑定本次登录，有效 5 分钟。',
      '管理员二次认证',
      {
        inputType: 'password',
        inputPattern: /.+/,
        inputErrorMessage: '请输入密码',
        confirmButtonText: '认证',
      },
    )
    await userStore.stepUp(result.value)
    if (!canManage.value) throw new Error('step-up permission unavailable')
    ElMessage.success('管理员高权限授权已生效')
    return true
  } catch {
    return false
  }
}

function handlePackageChange(file: UploadFile) {
  const raw = file.raw
  if (!raw) return
  if (!raw.name.toLowerCase().endsWith('.wcmupd')) {
    ElMessage.error('只能上传 .wcmupd 签名更新包')
    clearSelectedPackage()
    return
  }
  if (raw.size <= 0 || raw.size > MAX_PACKAGE_BYTES) {
    ElMessage.error('更新包必须大于 0 且不超过 100 MiB')
    clearSelectedPackage()
    return
  }
  uploadFile.value = raw
  uploadFiles.value = [file]
  uploadProgress.value = 0
}

function clearSelectedPackage() {
  uploadFile.value = null
  uploadFiles.value = []
  uploadProgress.value = 0
}

async function submitPackage() {
  if (!uploadFile.value) {
    ElMessage.warning('请选择 .wcmupd 文件')
    return
  }
  if (!await ensureStepUp()) return
  uploading.value = true
  uploadProgress.value = 0
  try {
    await updatesApi.createPackage(
      uploadFile.value,
      uploadDescription.value.trim(),
      event => {
        if (event.total) uploadProgress.value = Math.round(event.loaded / event.total * 100)
      },
    )
    ElMessage.success('更新包已上传并通过签名及内容校验')
    uploadVisible.value = false
    uploadDescription.value = ''
    clearSelectedPackage()
    await loadPackages()
  } finally {
    uploading.value = false
  }
}

async function deletePackage(pkg: UpdatePackage) {
  if (!await ensureStepUp()) return
  await ElMessageBox.confirm(
    `删除更新包 ${pkg.version}（${pkg.release_id || pkg.filename || pkg.id}）？正在使用的包会被服务端拒绝删除。`,
    '删除更新包',
    { type: 'warning', confirmButtonText: '删除' },
  )
  await updatesApi.deletePackage(pkg.id)
  ElMessage.success('更新包已删除')
  await loadPackages()
}

function resetDeploymentForm() {
  Object.assign(deploymentForm, {
    package_id: verifiedPackages.value[0]?.id || '',
    target_node_ids: [],
    target_group_ids: [],
    all_online: false,
    canary_node_ids: [],
  })
  resolved.value = null
}

function openCreateDeployment() {
  if (!verifiedPackages.value.length) {
    ElMessage.warning('请先上传并验证一个签名更新包')
    return
  }
  resetDeploymentForm()
  createVisible.value = true
}

async function resolveTargetSet() {
  if (!deploymentForm.package_id) {
    ElMessage.warning('请选择更新包')
    return null
  }
  if (
    !deploymentForm.target_node_ids.length
    && !deploymentForm.target_group_ids.length
    && !deploymentForm.all_online
  ) {
    ElMessage.warning('请选择节点、分组或全部在线节点')
    return null
  }
  resolving.value = true
  try {
    resolved.value = (await updatesApi.resolveTargets({
      package_id: deploymentForm.package_id,
      target_node_ids: deploymentForm.target_node_ids,
      target_group_ids: deploymentForm.target_group_ids,
      all_online: deploymentForm.all_online,
    })).data
    deploymentForm.canary_node_ids = []
    if (allTargetsCompatible.value) {
      ElMessage.success(`已解析 ${resolvedNodes.value.length} 个兼容 Agent`)
    } else {
      ElMessage.warning('目标中存在离线或环境不兼容节点，不能创建发布任务')
    }
    return resolved.value
  } finally {
    resolving.value = false
  }
}

async function createDeployment() {
  const targetResolution = resolved.value || await resolveTargetSet()
  if (!targetResolution || !allTargetsCompatible.value) return
  const resolvedIds = new Set(compatibleResolvedNodes.value.map(resolvedNodeId))
  const canaryIds = deploymentForm.canary_node_ids.filter(id => resolvedIds.has(id))
  if (!canaryIds.length) {
    ElMessage.warning('必须从解析后的兼容 Agent 中选择至少一个 Canary')
    return
  }
  if (!await ensureStepUp()) return
  await ElMessageBox.confirm(
    resolvedNodes.value.length === 1
      ? '单节点将直接分块传输、激活并执行健康检查。'
      : `先更新 ${canaryIds.length} 个 Canary；全部健康后任务会暂停，等待再次批准剩余节点。`,
    '创建 Agent 发布任务',
    { type: 'warning', confirmButtonText: '创建发布' },
  )
  creating.value = true
  try {
    const created = (await updatesApi.createDeployment({
      package_id: deploymentForm.package_id,
      target_node_ids: deploymentForm.target_node_ids,
      target_group_ids: deploymentForm.target_group_ids,
      all_online: deploymentForm.all_online,
      canary_node_ids: canaryIds,
    })).data
    createVisible.value = false
    ElMessage.success('发布任务已创建')
    await loadDeployments()
    await showDetail(created)
  } finally {
    creating.value = false
  }
}

async function showDetail(deployment: UpdateDeployment) {
  selectedDetailTargets.value = []
  detailVisible.value = true
  await loadDetail(deployment.id)
}

function handleTargetSelection(rows: UpdateDeploymentTarget[]) {
  selectedDetailTargets.value = rows
}

function targetSelectable(target: UpdateDeploymentTarget) {
  return canRetryUpdateTarget(target) || canRollbackUpdateTarget(target)
}

async function approveCurrent() {
  if (!detail.value || !await ensureStepUp()) return
  await ElMessageBox.confirm(
    'Canary 已健康。确认后将以每批最多 4 个 Agent 继续发布；任何失败都会暂停扩大范围。',
    '批准剩余节点',
    { type: 'warning', confirmButtonText: '批准发布' },
  )
  detail.value = (await updatesApi.approveDeployment(detail.value.id)).data
  ElMessage.success('已批准剩余节点')
  await loadDeployments(false)
}

async function cancelCurrent() {
  if (!detail.value || !await ensureStepUp()) return
  await ElMessageBox.confirm(
    '取消只阻止尚未激活的节点；正在原子切换的节点会在健康检查或回滚后结束。',
    '取消发布任务',
    { type: 'warning', confirmButtonText: '发送取消请求' },
  )
  detail.value = (await updatesApi.cancelDeployment(detail.value.id)).data
  ElMessage.success('取消请求已发送')
  await loadDeployments(false)
}

async function retrySelected() {
  if (!detail.value) return
  if (!selectedRetryNodeIds.value.length) {
    ElMessage.warning('请勾选 failed 或 paused 节点')
    return
  }
  if (!await ensureStepUp()) return
  await ElMessageBox.confirm(
    `使用新的执行尝试重试 ${selectedRetryNodeIds.value.length} 个节点？`,
    '重试失败节点',
    { type: 'warning', confirmButtonText: '重试' },
  )
  detail.value = (
    await updatesApi.retryDeployment(detail.value.id, selectedRetryNodeIds.value)
  ).data
  selectedDetailTargets.value = []
  ElMessage.success('所选节点已重新加入发布任务')
  await loadDeployments(false)
}

async function rollbackSelected() {
  if (!detail.value) return
  if (!selectedRollbackNodeIds.value.length) {
    ElMessage.warning('请勾选已完成激活的节点')
    return
  }
  if (!await ensureStepUp()) return
  await ElMessageBox.confirm(
    `将 ${selectedRollbackNodeIds.value.length} 个节点切换回上一个已验证版本并重启 Agent。`,
    '手动回滚',
    { type: 'error', confirmButtonText: '确认回滚' },
  )
  detail.value = (
    await updatesApi.rollbackDeployment(detail.value.id, selectedRollbackNodeIds.value)
  ).data
  selectedDetailTargets.value = []
  ElMessage.success('回滚任务已创建')
  await loadDeployments(false)
}

function packageLabel(packageId: string) {
  const pkg = packageMap.value.get(packageId)
  return pkg ? `${pkg.version} · ${pkg.release_id || pkg.filename || pkg.id}` : packageId
}

function resolvedNodeLabel(node: UpdateResolvedNode) {
  const nodeId = resolvedNodeId(node)
  const stored = nodeStore.nodes.find(item => item.id === nodeId)
  const hostname = node.hostname || stored?.hostname || nodeId
  const ip = node.ip || stored?.ip
  return ip ? `${hostname} (${ip})` : hostname
}

function nodeLabel(nodeId: string) {
  const node = nodeStore.nodes.find(item => item.id === nodeId)
  return node ? `${node.hostname || node.ip} (${node.ip})` : nodeId
}

function packageStatusLabel(status: string) {
  const labels: Record<string, string> = {
    verified: '可信可发布',
    legacy_untrusted: '旧版不可信',
    rejected: '校验失败',
  }
  return labels[status] || status
}

function progressValue(deployment: UpdateDeployment) {
  if (Number.isFinite(deployment.progress)) return Math.max(0, Math.min(100, deployment.progress))
  if (!deployment.targets?.length) return 0
  return Math.round(
    deployment.targets.reduce((total, item) => total + (item.progress || 0), 0)
    / deployment.targets.length,
  )
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function formatTime(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString() : '—'
}

function shortHash(value: string | null) {
  return value ? `${value.slice(0, 12)}…` : '—'
}

function canCancelStatus(status: string) {
  return [
    'queued',
    'transferring',
    'activating_canary',
    'awaiting_approval',
    'activating',
    'paused',
  ].includes(status)
}
</script>

<template>
  <div class="updates-page">
    <div class="page-header">
      <div>
        <h2>可信 Agent 更新中心</h2>
        <p>签名包只更新注册 Agent，不更新 Server、前端、Broker 或 Docker 容器。</p>
      </div>
      <el-space wrap>
        <el-tag :type="connected ? 'success' : 'warning'">
          {{ connected ? '实时进度已连接' : '实时连接断开，8 秒轮询兜底' }}
        </el-tag>
        <el-button v-if="userStore.isAdmin" @click="uploadVisible = true">上传签名包</el-button>
        <el-button v-if="userStore.isAdmin" type="primary" @click="openCreateDeployment">
          创建发布
        </el-button>
      </el-space>
    </div>

    <el-alert
      type="warning"
      show-icon
      :closable="false"
      title="更新中心不会持有签名私钥。只有通过离线 Ed25519 签名并完成内容校验的 .wcmupd 包可以发布。"
      class="page-alert"
    />

    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <strong>更新包</strong>
          <el-button @click="loadPackages()">刷新</el-button>
        </div>
      </template>
      <el-table v-loading="packagesLoading" :data="packages" row-key="id" stripe>
        <el-table-column label="版本 / Release" min-width="210">
          <template #default="{ row }">
            <div class="primary-text">{{ row.version }}</div>
            <span class="muted">{{ row.release_id || row.filename || row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="目标环境" min-width="230">
          <template #default="{ row }">
            <el-space wrap>
              <el-tag size="small">{{ row.target_os || '未知 OS' }}</el-tag>
              <el-tag size="small" type="info">{{ row.target_arch || '未知架构' }}</el-tag>
              <el-tag size="small" type="info">{{ row.python_abi || '未知 ABI' }}</el-tag>
            </el-space>
          </template>
        </el-table-column>
        <el-table-column label="信任状态" width="140">
          <template #default="{ row }">
            <el-tag :type="updateStatusType(row.verification_status)">
              {{ packageStatusLabel(row.verification_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Key ID / SHA-256" min-width="180">
          <template #default="{ row }">
            <div>{{ row.key_id || '—' }}</div>
            <span class="muted mono">{{ shortHash(row.sha256) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column label="上传时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created) }}</template>
        </el-table-column>
        <el-table-column v-if="userStore.isAdmin" label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="deletePackage(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!packagesLoading && !packages.length" description="暂无更新包" />
    </el-card>

    <el-card class="section-card">
      <template #header>
        <div class="card-header">
          <strong>发布历史</strong>
          <el-button @click="loadDeployments()">刷新</el-button>
        </div>
      </template>
      <el-table v-loading="deploymentsLoading" :data="deployments" row-key="id" stripe>
        <el-table-column label="更新包" min-width="230">
          <template #default="{ row }">
            <div class="primary-text">{{ packageLabel(row.package_id) }}</div>
            <span class="muted mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-tag :type="updateStatusType(row.status)">{{ updateStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总体进度" min-width="190">
          <template #default="{ row }">
            <el-progress
              :percentage="progressValue(row)"
              :status="row.status === 'failed' ? 'exception' : row.status === 'completed' ? 'success' : undefined"
            />
            <span class="muted">{{ row.targets?.length || 0 }} 个 Agent</span>
          </template>
        </el-table-column>
        <el-table-column label="Canary" width="90">
          <template #default="{ row }">{{ row.canary_node_ids?.length || 0 }}</template>
        </el-table-column>
        <el-table-column v-if="userStore.isAdmin" prop="created_by_name" label="创建者" width="120" />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="showDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!deploymentsLoading && !deployments.length" description="暂无发布记录" />
    </el-card>

    <el-dialog v-model="uploadVisible" title="上传可信更新包" width="620px">
      <el-upload
        v-model:file-list="uploadFiles"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".wcmupd"
        :on-change="handlePackageChange"
        :on-remove="clearSelectedPackage"
      >
        <div class="upload-title">拖拽 .wcmupd 到这里，或点击选择</div>
        <template #tip>
          <div class="el-upload__tip">最大 100 MiB；Server 会流式写入并重新校验签名、清单和每个文件。</div>
        </template>
      </el-upload>
      <el-form label-position="top" class="upload-form">
        <el-form-item label="发布说明">
          <el-input
            v-model="uploadDescription"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            placeholder="不写入更新包内容，仅作为服务端发布说明"
          />
        </el-form-item>
      </el-form>
      <el-progress v-if="uploading || uploadProgress" :percentage="uploadProgress" />
      <template #footer>
        <el-button :disabled="uploading" @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitPackage">
          上传并校验
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="创建 Agent 发布" width="780px">
      <el-form label-width="120px">
        <el-form-item label="可信更新包" required>
          <el-select v-model="deploymentForm.package_id" filterable style="width: 100%">
            <el-option
              v-for="pkg in verifiedPackages"
              :key="pkg.id"
              :label="`${pkg.version} · ${pkg.release_id || pkg.filename} · ${pkg.target_os}/${pkg.target_arch}/${pkg.python_abi}`"
              :value="pkg.id"
            />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">目标（服务端展开、去重，最多 32 个 Agent）</el-divider>
        <el-form-item label="节点">
          <el-select
            v-model="deploymentForm.target_node_ids"
            multiple
            filterable
            collapse-tags
            style="width: 100%"
          >
            <el-option
              v-for="node in nodeStore.nodes"
              :key="node.id"
              :label="`${node.hostname || node.ip} · ${node.status} · ${node.os || node.platform} · ${node.version || '未知版本'}`"
              :value="node.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="分组">
          <el-select
            v-model="deploymentForm.target_group_ids"
            multiple
            filterable
            collapse-tags
            style="width: 100%"
          >
            <el-option
              v-for="group in groups"
              :key="group.id"
              :label="`${group.name} (${group.node_count ?? 0})`"
              :value="group.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="全部在线">
          <el-checkbox v-model="deploymentForm.all_online">包含全部在线 Agent</el-checkbox>
          <el-button :loading="resolving" class="resolve-button" @click="resolveTargetSet">
            解析兼容性
          </el-button>
        </el-form-item>

        <template v-if="resolved">
          <el-alert
            :type="allTargetsCompatible ? 'success' : 'warning'"
            :closable="false"
            show-icon
            class="resolution-alert"
            :title="allTargetsCompatible
              ? `已解析 ${resolvedNodes.length} 个兼容 Agent`
              : '存在离线或不兼容 Agent，发布创建已阻止'"
          />
          <el-table :data="resolvedNodes" size="small" max-height="260" class="resolution-table">
            <el-table-column label="Agent" min-width="230">
              <template #default="{ row }">{{ resolvedNodeLabel(row) }}</template>
            </el-table-column>
            <el-table-column label="当前版本" width="110">
              <template #default="{ row }">{{ row.version || '—' }}</template>
            </el-table-column>
            <el-table-column label="兼容性" width="100">
              <template #default="{ row }">
                <el-tag :type="row.compatible ? 'success' : 'danger'" size="small">
                  {{ row.compatible ? '兼容' : '不兼容' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="原因" min-width="220">
              <template #default="{ row }">
                {{ incompatibilityReasons(row).join('；') || '—' }}
              </template>
            </el-table-column>
          </el-table>

          <el-form-item label="Canary" required class="canary-select">
            <el-select
              v-model="deploymentForm.canary_node_ids"
              multiple
              filterable
              :disabled="!allTargetsCompatible"
              placeholder="至少选择一个先行节点"
              style="width: 100%"
            >
              <el-option
                v-for="node in compatibleResolvedNodes"
                :key="resolvedNodeId(node)"
                :label="resolvedNodeLabel(node)"
                :value="resolvedNodeId(node)"
              />
            </el-select>
          </el-form-item>
        </template>
        <el-alert
          type="info"
          :closable="false"
          title="多节点任务在 Canary 全部健康后暂停，必须再次进行管理员二次认证并批准，才会继续剩余节点。"
        />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createDeployment">创建发布</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="发布详情" size="86%">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="3" border>
            <el-descriptions-item label="更新包">{{ packageLabel(detail.package_id) }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="updateStatusType(detail.status)">{{ updateStatusLabel(detail.status) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="总体进度">
              {{ progressValue(detail) }}%
            </el-descriptions-item>
            <el-descriptions-item label="发布 ID"><span class="mono">{{ detail.id }}</span></el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatTime(detail.created) }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ formatTime(detail.updated) }}</el-descriptions-item>
          </el-descriptions>

          <el-alert
            v-if="detail.error"
            type="error"
            :closable="false"
            :title="detail.error"
            class="detail-alert"
          />

          <div v-if="userStore.isAdmin" class="detail-actions">
            <el-button
              v-if="detail.status === 'awaiting_approval'"
              type="danger"
              @click="approveCurrent"
            >
              批准剩余节点
            </el-button>
            <el-button
              v-if="canCancelStatus(detail.status)"
              type="warning"
              @click="cancelCurrent"
            >
              取消发布
            </el-button>
            <el-button
              type="primary"
              :disabled="!selectedRetryNodeIds.length"
              @click="retrySelected"
            >
              重试所选失败节点
            </el-button>
            <el-button
              type="danger"
              plain
              :disabled="!selectedRollbackNodeIds.length"
              @click="rollbackSelected"
            >
              回滚所选成功节点
            </el-button>
          </div>

          <el-table
            :data="detail.targets || []"
            row-key="node_id"
            @selection-change="handleTargetSelection"
          >
            <el-table-column
              v-if="userStore.isAdmin"
              type="selection"
              width="48"
              :selectable="targetSelectable"
            />
            <el-table-column label="Agent" min-width="220">
              <template #default="{ row }">
                {{ nodeLabel(row.node_id) }}
                <el-tag v-if="row.is_canary || detail.canary_node_ids?.includes(row.node_id)" size="small">
                  Canary
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态 / 阶段" width="180">
              <template #default="{ row }">
                <el-tag :type="updateStatusType(row.status)" size="small">
                  {{ updateStatusLabel(row.status) }}
                </el-tag>
                <div class="muted">{{ updateStatusLabel(row.phase || '') }}</div>
              </template>
            </el-table-column>
            <el-table-column label="进度" min-width="160">
              <template #default="{ row }">
                <el-progress
                  :percentage="Math.max(0, Math.min(100, row.progress || 0))"
                  :status="row.status === 'failed' ? 'exception' : row.status === 'completed' ? 'success' : undefined"
                />
              </template>
            </el-table-column>
            <el-table-column prop="attempts" label="尝试" width="70" />
            <el-table-column label="版本" min-width="160">
              <template #default="{ row }">
                {{ row.from_version || '—' }} → {{ row.to_version || '—' }}
              </template>
            </el-table-column>
            <el-table-column label="回滚" width="110">
              <template #default="{ row }">{{ row.rollback_status ? updateStatusLabel(row.rollback_status) : '—' }}</template>
            </el-table-column>
            <el-table-column label="消息 / 错误" min-width="250">
              <template #default="{ row }">
                <span :class="{ error: row.error }">{{ row.error || row.message || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="更新时间" width="180">
              <template #default="{ row }">{{ formatTime(row.updated || row.completed) }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!detail.targets?.length" description="暂无逐节点状态" />
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-header,
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
}
.page-header p {
  margin: 0;
  color: var(--el-text-color-secondary);
}
.page-alert,
.section-card {
  margin-top: 16px;
}
.primary-text {
  font-weight: 600;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.upload-title {
  padding: 30px 0 12px;
}
.upload-form {
  margin-top: 18px;
}
.resolve-button {
  margin-left: 16px;
}
.resolution-alert,
.resolution-table,
.canary-select {
  margin-bottom: 16px;
}
.detail-alert {
  margin-top: 16px;
}
.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 18px 0;
}
.error {
  color: var(--el-color-danger);
}
</style>
