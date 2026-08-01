<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import {
  terminalApi,
  type TerminalCapabilities,
  type TerminalNodeCapability,
} from '../api/terminal'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const capabilities = ref<TerminalCapabilities | null>(null)
const mode = ref<'low' | 'admin'>('low')
const nodeId = ref('')
const profile = ref('')
const state = ref<'idle' | 'connecting' | 'connected' | 'closed' | 'error'>('idle')
const exitText = ref('')
const terminalElement = ref<HTMLElement | null>(null)
let socket: WebSocket | null = null
let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let resizeObserver: ResizeObserver | null = null
let dataDisposable: { dispose(): void } | null = null
let resizeDisposable: { dispose(): void } | null = null

const availableNodes = computed(() =>
  (capabilities.value?.nodes || []).filter(node =>
    mode.value === 'low' ? node.low_available : node.admin_available,
  ),
)
const selectedNode = computed<TerminalNodeCapability | undefined>(() =>
  capabilities.value?.nodes.find(node => node.node_id === nodeId.value),
)
const canStart = computed(() =>
  state.value !== 'connecting'
  && state.value !== 'connected'
  && Boolean(nodeId.value)
  && (mode.value === 'admin' || Boolean(profile.value)),
)

async function loadCapabilities() {
  const response = await terminalApi.capabilities()
  capabilities.value = response.data
  const nodes = availableNodes.value
  if (!nodes.some(node => node.node_id === nodeId.value)) {
    nodeId.value = nodes[0]?.node_id || ''
  }
  if (mode.value === 'low') {
    profile.value = selectedNode.value?.low_profiles[0] || ''
  }
}

function configureTerminal() {
  if (terminal || !terminalElement.value) return
  terminal = new Terminal({
    convertEol: true,
    cursorBlink: true,
    disableStdin: true,
    fontFamily: 'Cascadia Mono, Consolas, monospace',
    fontSize: 14,
    scrollback: 5000,
    theme: { background: '#0b1020', foreground: '#d7e0f0' },
  })
  fitAddon = new FitAddon()
  terminal.loadAddon(fitAddon)
  terminal.open(terminalElement.value)
  fitAddon.fit()
  dataDisposable = terminal.onData(data => {
    if (mode.value === 'admin' && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }))
    }
  })
  resizeDisposable = terminal.onResize(({ rows, cols }) => {
    if (mode.value === 'admin' && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'resize', rows, cols }))
    }
  })
  resizeObserver = new ResizeObserver(() => fitAddon?.fit())
  resizeObserver.observe(terminalElement.value)
}

async function ensureAdminStepUp() {
  if (userStore.hasPermission('terminal.admin')) return
  const result = await ElMessageBox.prompt(
    '请输入当前管理员密码。授权仅绑定本次登录，会在 5 分钟后失效。',
    '管理员二次认证',
    {
      inputType: 'password',
      inputPattern: /.+/,
      inputErrorMessage: '请输入密码',
      confirmButtonText: '认证',
      cancelButtonText: '取消',
    },
  )
  await userStore.stepUp(result.value)
  if (!userStore.hasPermission('terminal.admin')) {
    throw new Error('二次认证未生效')
  }
}

async function start() {
  if (!canStart.value) return
  if (mode.value === 'admin') {
    await ensureAdminStepUp()
    await ElMessageBox.confirm(
      '这将打开宿主机 root/SYSTEM Shell。命令不会被系统保存，但会直接影响目标设备。',
      '高权限终端确认',
      { type: 'warning', confirmButtonText: '继续连接' },
    )
  }
  configureTerminal()
  terminal?.reset()
  terminal!.options.disableStdin = mode.value !== 'admin'
  terminal?.writeln(
    mode.value === 'admin'
      ? '\x1b[31m正在连接独立特权 Broker…\x1b[0m'
      : `正在运行批准别名：${profile.value}`,
  )
  state.value = 'connecting'
  exitText.value = ''
  const response = await terminalApi.ticket({
    node_id: nodeId.value,
    mode: mode.value,
    ...(mode.value === 'low' ? { profile: profile.value } : {}),
  })
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  socket = new WebSocket(`${scheme}//${window.location.host}${response.data.websocket_path}`)
  socket.addEventListener('open', () => {
    socket?.send(JSON.stringify({ type: 'auth', ticket: response.data.ticket }))
  })
  socket.addEventListener('message', event => {
    let message: Record<string, unknown>
    try {
      message = JSON.parse(String(event.data))
    } catch {
      return
    }
    if (message.type === 'ready') {
      state.value = 'connected'
      terminal?.writeln('\r\n\x1b[32m连接已建立\x1b[0m')
      nextTick(() => fitAddon?.fit())
    } else if (message.type === 'data' && typeof message.data === 'string') {
      terminal?.write(message.data)
    } else if (message.type === 'exit') {
      exitText.value = `进程已退出（${message.exit_code ?? -1}）`
      terminal?.writeln(`\r\n\x1b[33m${exitText.value}\x1b[0m`)
      state.value = 'closed'
    } else if (message.type === 'error') {
      const text = typeof message.message === 'string' ? message.message : '终端错误'
      terminal?.writeln(`\r\n\x1b[31m${text}\x1b[0m`)
      state.value = 'error'
    }
  })
  socket.addEventListener('close', () => {
    if (state.value === 'connected' || state.value === 'connecting') state.value = 'closed'
    socket = null
  })
  socket.addEventListener('error', () => {
    state.value = 'error'
    ElMessage.error('终端 WebSocket 连接失败')
  })
}

function close() {
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: 'close' }))
    socket.close()
  }
  socket = null
  state.value = 'closed'
}

function switchMode(value: string | number | boolean | undefined) {
  close()
  mode.value = value === 'admin' ? 'admin' : 'low'
  loadCapabilities()
}

onMounted(async () => {
  configureTerminal()
  await loadCapabilities()
})
onBeforeUnmount(() => {
  close()
  resizeObserver?.disconnect()
  dataDisposable?.dispose()
  resizeDisposable?.dispose()
  terminal?.dispose()
})
</script>

<template>
  <div class="terminal-page">
    <div class="heading">
      <div>
        <h2>安全远程终端</h2>
        <p>普通模式只能运行固定别名；管理员模式通过独立 Broker 打开宿主机 Shell。</p>
      </div>
      <el-tag :type="state === 'connected' ? 'success' : state === 'error' ? 'danger' : 'info'">
        {{ state }}
      </el-tag>
    </div>

    <el-alert
      v-if="capabilities && !capabilities.secure_transport"
      type="error"
      :closable="false"
      title="当前连接不满足 HTTPS/WSS 要求，终端已禁用。"
      class="notice"
    />

    <el-card class="controls">
      <el-form inline>
        <el-form-item label="模式">
          <el-radio-group :model-value="mode" :disabled="state === 'connected'" @change="switchMode">
            <el-radio-button value="low">普通别名</el-radio-button>
            <el-radio-button v-if="userStore.isAdmin" value="admin">管理员 Shell</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="宿主机">
          <el-select v-model="nodeId" style="width: 260px" @change="loadCapabilities">
            <el-option
              v-for="node in availableNodes"
              :key="node.node_id"
              :label="`${node.hostname} (${node.platform})`"
              :value="node.node_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="mode === 'low'" label="批准别名">
          <el-select v-model="profile" style="width: 220px">
            <el-option
              v-for="item in selectedNode?.low_profiles || []"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :disabled="!canStart" @click="start">启动</el-button>
          <el-button :disabled="state !== 'connected' && state !== 'connecting'" @click="close">
            关闭
          </el-button>
        </el-form-item>
      </el-form>
      <p v-if="mode === 'low'" class="hint">
        只读输出；不能输入命令、参数、工作目录或环境变量。
      </p>
      <p v-else class="danger">
        高权限模式需要管理员密码二次认证，最长 30 分钟，空闲 10 分钟自动关闭。
      </p>
    </el-card>

    <div ref="terminalElement" class="terminal-host" />
  </div>
</template>

<style scoped>
.terminal-page { display: grid; gap: 14px; }
.heading { display: flex; align-items: center; justify-content: space-between; }
.heading h2 { margin: 0 0 6px; }
.heading p, .hint { margin: 0; color: var(--el-text-color-secondary); }
.notice { margin-bottom: 2px; }
.controls :deep(.el-card__body) { padding-bottom: 12px; }
.danger { margin: 0; color: var(--el-color-danger); }
.terminal-host {
  height: min(62vh, 680px);
  min-height: 380px;
  padding: 10px;
  border-radius: 8px;
  background: #0b1020;
  overflow: hidden;
}
</style>
