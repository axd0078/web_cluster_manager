<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentsApi, type EnrollmentTokenInfo } from '../api/agents'
import { authApi } from '../api/auth'
import { nodesApi } from '../api/nodes'
import { terminalApi } from '../api/terminal'
import { useUserStore } from '../stores/user'

const user = useUserStore()
const label = ref('')
const ttlMinutes = ref(15)
const creating = ref(false)
const rawToken = ref('')
const expiresAt = ref('')
const tokens = ref<EnrollmentTokenInfo[]>([])
const passwordForm = ref({ current: '', next: '' })
const brokers = ref<any[]>([])
const brokerNodes = ref<any[]>([])
const selectedBrokerNode = ref('')
const rawBrokerToken = ref('')
const users = ref<any[]>([])
const userForm = ref({ username: '', password: '', role: 'user' as 'user' | 'admin' })

const apiUrl = computed(() => `${location.origin}/api/v2`)
const wsUrl = computed(() => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/agent`)
const installHint = computed(() => rawToken.value ? [
  '# Windows PowerShell',
  `$env:WCM_ENROLLMENT_TOKEN='${rawToken.value}'`,
  `python agent/main.py --api '${apiUrl.value}' --server '${wsUrl.value}' --ca-cert 'C:\\path\\internal-ca.crt'`,
  '',
  '# Linux shell',
  `export WCM_ENROLLMENT_TOKEN='${rawToken.value}'`,
  `python3 agent/main.py --api '${apiUrl.value}' --server '${wsUrl.value}' --ca-cert '/path/internal-ca.crt'`,
].join('\n') : '')

async function loadTokens() {
  if (!user.hasPermission('agents.manage')) return
  tokens.value = (await agentsApi.listEnrollmentTokens()).data
}

async function loadBrokers() {
  if (!user.hasPermission('brokers.read')) return
  const [brokerResponse, nodeResponse] = await Promise.all([
    terminalApi.brokers(),
    nodesApi.list(),
  ])
  brokers.value = brokerResponse.data
  brokerNodes.value = nodeResponse.data
}

async function loadUsers() {
  if (!user.hasPermission('users.read')) return
  users.value = (await authApi.listUsers()).data
}

async function createUser() {
  await authApi.createUser(
    userForm.value.username,
    userForm.value.password,
    userForm.value.role,
  )
  userForm.value = { username: '', password: '', role: 'user' }
  await loadUsers()
}

async function updateAccount(
  account: any,
  data: { role?: 'admin' | 'user'; disabled?: boolean },
) {
  await authApi.updateUser(account.id, data)
  await loadUsers()
}

async function rotateBrokerCredential() {
  const response = await terminalApi.rotateBrokerCredential(selectedBrokerNode.value)
  rawBrokerToken.value = response.data.broker_token
  ElMessage.warning('Broker 凭据原文只显示一次，请立即在目标宿主机完成安装')
  await loadBrokers()
}

async function revokeBrokerCredential(nodeId: string) {
  await ElMessageBox.confirm(
    '撤销后会立即断开 Broker 和该节点的高权限终端。',
    '撤销 Broker 凭据',
    { type: 'warning' },
  )
  await terminalApi.revokeBrokerCredential(nodeId)
  await loadBrokers()
}

async function createToken() {
  creating.value = true
  try {
    const response = await agentsApi.createEnrollmentToken(label.value, ttlMinutes.value)
    rawToken.value = response.data.token
    expiresAt.value = response.data.expires_at
    ElMessage.warning('令牌只显示这一次，请安全复制到目标设备')
    await loadTokens()
  } finally {
    creating.value = false
  }
}

async function revokeToken(token: EnrollmentTokenInfo) {
  await ElMessageBox.confirm('确认撤销该 Agent 注册令牌？', '安全确认', { type: 'warning' })
  await agentsApi.revokeEnrollmentToken(token.id)
  await loadTokens()
}

async function copyHint() {
  await navigator.clipboard.writeText(installHint.value)
  ElMessage.success('已复制注册命令')
}

async function changePassword() {
  await authApi.changePassword(passwordForm.value.current, passwordForm.value.next)
  passwordForm.value = { current: '', next: '' }
  ElMessage.success('密码已更新')
}

onMounted(() => Promise.all([loadTokens(), loadBrokers(), loadUsers()]))
watch(
  () => user.user?.permissions.join(','),
  () => Promise.all([loadTokens(), loadBrokers(), loadUsers()]),
)
</script>

<template>
  <div>
    <h2>系统设置与 Agent 安全注册</h2>
    <el-card header="修改当前密码" style="margin-bottom: 16px">
      <el-form inline>
        <el-form-item label="当前密码"><el-input v-model="passwordForm.current" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="passwordForm.next" type="password" show-password placeholder="至少 12 位" /></el-form-item>
        <el-form-item><el-button :disabled="passwordForm.next.length < 12" @click="changePassword">更新密码</el-button></el-form-item>
      </el-form>
    </el-card>
    <el-alert v-if="!user.hasPermission('agents.manage')" type="warning" :closable="false" title="签发或撤销 Agent 凭据需要管理员 5 分钟高权限授权" />
    <template v-else>
      <el-card header="签发一次性注册令牌" style="margin-bottom: 16px">
        <el-form inline>
          <el-form-item label="设备备注"><el-input v-model="label" maxlength="200" placeholder="例如：机房 A Docker 宿主机" /></el-form-item>
          <el-form-item label="有效分钟"><el-input-number v-model="ttlMinutes" :min="1" :max="1440" /></el-form-item>
          <el-form-item><el-button type="primary" :loading="creating" @click="createToken">生成令牌</el-button></el-form-item>
        </el-form>
        <el-alert v-if="rawToken" type="success" :closable="false" title="一次性注册信息">
          <p>过期时间：{{ expiresAt }}</p>
          <pre class="command">{{ installHint }}</pre>
          <el-button size="small" @click="copyHint">复制</el-button>
        </el-alert>
      </el-card>

      <el-card header="最近注册令牌">
        <el-table :data="tokens" size="small">
          <el-table-column prop="label" label="备注" />
          <el-table-column prop="created" label="创建时间" width="190" />
          <el-table-column prop="expires_at" label="过期时间" width="190" />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">{{ row.revoked_at ? '已撤销' : row.used_at ? '已使用' : '待使用' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }"><el-button v-if="!row.used_at && !row.revoked_at" size="small" type="danger" @click="revokeToken(row)">撤销</el-button></template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-card
      v-if="user.hasPermission('brokers.read')"
      header="独立特权 Broker"
      style="margin-top: 16px"
    >
      <el-alert
        type="warning"
        :closable="false"
        title="Broker 以 root/SYSTEM 运行，凭据与普通 Agent 完全独立。"
        style="margin-bottom: 12px"
      />
      <el-form v-if="user.hasPermission('brokers.manage')" inline>
        <el-form-item label="注册节点">
          <el-select v-model="selectedBrokerNode" style="width: 280px">
            <el-option
              v-for="node in brokerNodes"
              :key="node.id"
              :label="`${node.hostname || node.ip} (${node.platform})`"
              :value="node.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button
            type="danger"
            :disabled="!selectedBrokerNode"
            @click="rotateBrokerCredential"
          >
            生成/轮换 Broker 凭据
          </el-button>
        </el-form-item>
      </el-form>
      <el-alert
        v-if="rawBrokerToken"
        type="success"
        :closable="false"
        title="一次性显示的 Broker 凭据"
      >
        <pre class="command">{{ rawBrokerToken }}</pre>
      </el-alert>
      <el-table :data="brokers" size="small" style="margin-top: 12px">
        <el-table-column prop="node_id" label="节点 ID" min-width="260" />
        <el-table-column prop="credential_state" label="凭据状态" width="120" />
        <el-table-column label="连接" width="100">
          <template #default="{ row }">
            <el-tag :type="row.online ? 'success' : 'info'">
              {{ row.online ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_used_at" label="最近连接" width="200" />
        <el-table-column v-if="user.hasPermission('brokers.manage')" label="操作" width="100">
          <template #default="{ row }">
            <el-button
              v-if="row.credential_state !== 'revoked'"
              size="small"
              type="danger"
              @click="revokeBrokerCredential(row.node_id)"
            >
              撤销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card
      v-if="user.hasPermission('users.read')"
      header="用户与双身份"
      style="margin-top: 16px"
    >
      <el-form v-if="user.hasPermission('users.manage')" inline>
        <el-form-item label="用户名">
          <el-input v-model="userForm.username" maxlength="100" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="userForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="身份">
          <el-select v-model="userForm.role" style="width: 120px">
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :disabled="userForm.username.length < 3 || userForm.password.length < 12"
            @click="createUser"
          >
            创建用户
          </el-button>
        </el-form-item>
      </el-form>
      <el-table :data="users" size="small">
        <el-table-column prop="username" label="用户名" />
        <el-table-column label="身份" width="150">
          <template #default="{ row }">
            <el-select
              :model-value="row.role"
              :disabled="!user.hasPermission('users.manage')"
              @change="(role: 'admin' | 'user') => updateAccount(row, { role })"
            >
              <el-option label="普通用户" value="user" />
              <el-option label="管理员" value="admin" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.disabled ? 'danger' : 'success'">
              {{ row.disabled ? '已禁用' : '启用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="user.hasPermission('users.manage')" label="操作" width="120">
          <template #default="{ row }">
            <el-button
              v-if="row.id !== user.user?.id"
              size="small"
              :type="row.disabled ? 'success' : 'danger'"
              @click="updateAccount(row, { disabled: !row.disabled })"
            >
              {{ row.disabled ? '启用' : '禁用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.command { white-space: pre-wrap; word-break: break-all; padding: 12px; background: var(--el-fill-color-light); }
</style>
