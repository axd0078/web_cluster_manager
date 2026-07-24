<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { agentsApi, type EnrollmentTokenInfo } from '../api/agents'
import { authApi } from '../api/auth'
import { useUserStore } from '../stores/user'

const user = useUserStore()
const label = ref('')
const ttlMinutes = ref(15)
const creating = ref(false)
const rawToken = ref('')
const expiresAt = ref('')
const tokens = ref<EnrollmentTokenInfo[]>([])
const passwordForm = ref({ current: '', next: '' })

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
  if (!user.isAdmin) return
  tokens.value = (await agentsApi.listEnrollmentTokens()).data
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

onMounted(loadTokens)
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
    <el-alert v-if="!user.isAdmin" type="warning" :closable="false" title="仅管理员可以签发或撤销 Agent 注册令牌" />
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
  </div>
</template>

<style scoped>
.command { white-space: pre-wrap; word-break: break-all; padding: 12px; background: var(--el-fill-color-light); }
</style>
