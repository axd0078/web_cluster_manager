<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { UploadFile, UploadUserFile } from 'element-plus'
import { ElMessage, ElMessageBox } from 'element-plus'
import { filesApi, type FileTransfer, type UploadSession } from '../api/files'
import { useWebSocket } from '../composables/useWebSocket'
import { useNodeStore } from '../stores/nodes'
import { useUserStore } from '../stores/user'

const MAX_FILE_BYTES = 100 * 1024 * 1024

const nodeStore = useNodeStore()
const userStore = useUserStore()
const selectedFile = ref<File | null>(null)
const fileList = ref<UploadUserFile[]>([])
const targetNodeIds = ref<string[]>([])
const destinationPath = ref('')
const overwrite = ref(false)
const busy = ref(false)
const stage = ref('')
const uploadProgress = ref(0)
const uploads = ref<UploadSession[]>([])
const transfers = ref<FileTransfer[]>([])
const historyLoading = ref(false)
const { lastMessage } = useWebSocket()
let refreshTimer: ReturnType<typeof setInterval> | null = null

const canTransfer = computed(() => userStore.hasPermission('files.write_sandbox'))
const canOverwrite = computed(() => userStore.hasPermission('files.overwrite'))
const onlineNodes = computed(() => nodeStore.onlineNodes)

watch(lastMessage, message => {
  if (message?.type === 'file_transfer_progress') void loadTransfers(false)
})

onMounted(async () => {
  await Promise.all([nodeStore.fetchNodes(), loadUploads(), loadTransfers()])
  refreshTimer = setInterval(() => void loadTransfers(false), 3000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

function handleFileChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  if (raw.size <= 0) {
    ElMessage.error('不能传输空文件')
    fileList.value = []
    selectedFile.value = null
    return
  }
  if (raw.size > MAX_FILE_BYTES) {
    ElMessage.error('文件不能超过 100 MiB')
    fileList.value = []
    selectedFile.value = null
    return
  }
  selectedFile.value = raw
  fileList.value = [uploadFile]
  destinationPath.value = raw.name
  uploadProgress.value = 0
}

function handleFileRemove() {
  selectedFile.value = null
  fileList.value = []
  destinationPath.value = ''
  uploadProgress.value = 0
}

async function sha256(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map(value => value.toString(16).padStart(2, '0'))
    .join('')
}

async function startTransfer() {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (!targetNodeIds.value.length) {
    ElMessage.warning('请选择至少一个在线 Agent')
    return
  }
  if (!destinationPath.value.trim()) {
    ElMessage.warning('请输入沙箱内相对保存路径')
    return
  }

  const file = selectedFile.value
  busy.value = true
  try {
    stage.value = '正在计算完整文件 SHA-256'
    const fileHash = await sha256(file)
    stage.value = '正在创建或恢复上传会话'
    const uploadResponse = await filesApi.createUpload({
      filename: file.name,
      size: file.size,
      sha256: fileHash,
    })
    let session = uploadResponse.data
    let offset = session.received_bytes
    uploadProgress.value = Math.round(offset / file.size * 100)

    if (session.status !== 'ready') {
      while (offset < file.size) {
        const end = Math.min(offset + session.chunk_size, file.size)
        const chunk = file.slice(offset, end)
        stage.value = `正在上传服务端：${formatSize(offset)} / ${formatSize(file.size)}`
        const chunkHash = await sha256(chunk)
        try {
          const response = await filesApi.uploadChunk(session.id, offset, chunkHash, chunk)
          offset = response.data.next_offset
        } catch (error: any) {
          const detail = error?.response?.data?.detail
          const expected = typeof detail === 'object' ? detail?.expected_offset : undefined
          if (error?.response?.status === 409 && Number.isInteger(expected)) {
            offset = expected
            continue
          }
          throw error
        }
        uploadProgress.value = Math.round(offset / file.size * 100)
      }
      stage.value = '正在验证服务端完整文件'
      session = (await filesApi.completeUpload(session.id)).data
    }

    stage.value = '正在创建多节点分发任务'
    await filesApi.createTransfer({
      upload_id: session.id,
      target_node_ids: targetNodeIds.value,
      dest_path: destinationPath.value.trim(),
      overwrite: overwrite.value,
    })
    uploadProgress.value = 100
    stage.value = '分发任务已创建，可在下方查看逐节点进度'
    ElMessage.success('文件上传完成，已开始向 Agent 分发')
    await Promise.all([loadUploads(), loadTransfers()])
  } finally {
    busy.value = false
  }
}

async function loadUploads() {
  const response = await filesApi.listUploads()
  uploads.value = response.data
}

async function loadTransfers(showLoading = true) {
  if (showLoading) historyLoading.value = true
  try {
    const response = await filesApi.listTransfers()
    transfers.value = response.data
  } finally {
    if (showLoading) historyLoading.value = false
  }
}

async function retryTransfer(transfer: FileTransfer) {
  await ElMessageBox.confirm(
    `继续传输 ${transfer.filename} 到暂停或失败的节点？`,
    '断点续传确认',
    { type: 'warning' },
  )
  await filesApi.retryTransfer(transfer.id)
  ElMessage.success('已重新加入传输队列')
  await loadTransfers()
}

function canRetry(transfer: FileTransfer) {
  return transfer.targets.some(target => ['paused', 'failed'].includes(target.status))
}

function nodeLabel(nodeId: string) {
  const node = nodeStore.nodes.find(item => item.id === nodeId)
  return node ? `${node.hostname || '未知主机'} (${node.ip})` : nodeId
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    uploading: '上传中', verifying: '校验中', ready: '已就绪',
    queued: '排队中', running: '传输中', paused: '已暂停',
    partial: '部分完成', completed: '已完成', failed: '失败',
    cancelled: '已取消',
  }
  return labels[status] || status
}

function statusType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (status === 'completed' || status === 'ready') return 'success'
  if (status === 'running' || status === 'uploading' || status === 'verifying') return 'primary'
  if (status === 'queued' || status === 'paused' || status === 'partial') return 'warning'
  if (status === 'failed') return 'danger'
  return 'info'
}
</script>

<template>
  <div>
    <div class="page-title">
      <div>
        <h2>安全文件传输</h2>
        <p>浏览器分块上传后，可分发到一个或多个 Agent 的固定沙箱目录。</p>
      </div>
      <el-button @click="loadTransfers()">刷新记录</el-button>
    </div>

    <el-alert
      type="info" :closable="false" show-icon
      title="Docker 容器不是直接文件目标；Docker 宿主机上的 Agent 可以接收文件。目标路径只能是 transfer_root 下的相对路径。"
      style="margin-bottom: 16px"
    />

    <el-card v-if="canTransfer" class="transfer-card">
      <template #header><strong>新建传输</strong></template>
      <el-row :gutter="20">
        <el-col :xs="24" :lg="10">
          <el-upload
            v-model:file-list="fileList"
            drag :auto-upload="false" :limit="1"
            :on-change="handleFileChange" :on-remove="handleFileRemove"
          >
            <div class="upload-text">拖拽文件到这里，或点击选择</div>
            <template #tip>
              <div class="el-upload__tip">单文件最大 100 MiB；使用 SHA-256 与 512 KiB 分块校验。</div>
            </template>
          </el-upload>
        </el-col>
        <el-col :xs="24" :lg="14">
          <el-form label-position="top">
            <el-form-item label="目标 Agent">
              <el-select
                v-model="targetNodeIds" multiple filterable
                placeholder="选择一个或多个在线 Agent" style="width: 100%"
              >
                <el-option
                  v-for="node in onlineNodes" :key="node.id"
                  :label="`${node.hostname || '未知主机'} (${node.ip}) · ${node.os || node.platform}`"
                  :value="node.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="沙箱内相对保存路径">
              <el-input v-model="destinationPath" placeholder="例如 packages/app.zip">
                <template #prepend>transfer_root/</template>
              </el-input>
            </el-form-item>
            <el-form-item>
              <el-checkbox v-model="overwrite" :disabled="!canOverwrite">
                允许原子覆盖同名文件（需要管理员二次认证）
              </el-checkbox>
            </el-form-item>
            <el-button type="primary" :loading="busy" @click="startTransfer">
              上传并开始分发
            </el-button>
          </el-form>
        </el-col>
      </el-row>
      <div v-if="stage" class="stage">
        <span>{{ stage }}</span>
        <el-progress :percentage="uploadProgress" />
      </div>
    </el-card>

    <el-card class="history-card">
      <template #header>
        <div class="card-header">
          <strong>传输记录</strong>
          <span>服务端源文件默认保留 7 天，失败节点可在此续传。</span>
        </div>
      </template>
      <el-table :data="transfers" v-loading="historyLoading" row-key="id">
        <el-table-column type="expand">
          <template #default="{ row }">
            <el-table :data="row.targets" size="small" class="target-table">
              <el-table-column label="Agent" min-width="220">
                <template #default="{ row: target }">{{ nodeLabel(target.node_id) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row: target }">
                  <el-tag :type="statusType(target.status)" size="small">{{ statusLabel(target.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="进度" min-width="180">
                <template #default="{ row: target }">
                  <el-progress :percentage="target.progress" :status="target.status === 'failed' ? 'exception' : undefined" />
                </template>
              </el-table-column>
              <el-table-column prop="attempts" label="尝试" width="70" />
              <el-table-column label="错误" min-width="240">
                <template #default="{ row: target }">{{ target.error || '—' }}</template>
              </el-table-column>
            </el-table>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件" min-width="180" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
        <el-table-column prop="dest_path" label="目标相对路径" min-width="180" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canTransfer && canRetry(row)" size="small" type="warning"
              @click="retryTransfer(row)"
            >续传</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!historyLoading && !transfers.length" description="暂无文件传输记录" />
    </el-card>

    <el-card class="history-card">
      <template #header><strong>最近上传会话</strong></template>
      <el-table :data="uploads.slice(0, 10)" size="small">
        <el-table-column prop="filename" label="文件" min-width="200" />
        <el-table-column label="进度" min-width="200">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.received_bytes / row.size * 100)" />
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updated) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.page-title, .card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.page-title h2 { margin: 0 0 4px; }
.page-title p, .card-header span {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.transfer-card, .history-card { margin-bottom: 16px; }
.upload-text { padding: 28px 0 12px; color: var(--el-text-color-regular); }
.stage {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--el-border-color-light);
}
.stage span {
  display: block;
  margin-bottom: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.target-table { margin: 8px 24px 16px; width: calc(100% - 48px); }
</style>
