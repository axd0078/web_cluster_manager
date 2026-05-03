<template>
  <el-container class="app-layout">
    <!-- 顶栏 -->
    <el-header class="app-header">
      <div class="header-left">
        <el-button :icon="Fold" @click="collapsed = !collapsed" text />
        <span class="logo-text">集群管理系统</span>
      </div>
      <div class="header-right">
        <el-tag :type="wsConnected ? 'success' : 'danger'" size="small" effect="dark">
          {{ wsConnected ? '已连接' : '未连接' }}
        </el-tag>
        <el-dropdown trigger="click">
          <span class="user-dropdown">
            <el-icon><UserFilled /></el-icon>
            {{ userStore.username }}
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="handleLogout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <el-container>
      <!-- 侧栏 -->
      <el-aside :width="collapsed ? '64px' : '200px'" class="app-sidebar">
        <el-menu
          :default-active="activeMenu"
          :collapse="collapsed"
          router
          class="sidebar-menu"
        >
          <el-menu-item index="/dashboard">
            <el-icon><DataBoard /></el-icon>
            <span>仪表盘</span>
          </el-menu-item>
          <el-menu-item index="/nodes">
            <el-icon><Monitor /></el-icon>
            <span>节点管理</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <!-- 内容区 -->
      <el-main class="app-main">
        <router-view :wsConnected="wsConnected" :wsSend="wsSend" :wsOn="wsOn" :wsOff="wsOff" />
      </el-main>
    </el-container>

    <!-- 状态栏 -->
    <el-footer class="app-footer">
      <span>🟢 {{ onlineCount }} 在线</span>
      <span>🔴 {{ offlineCount }} 离线</span>
      <span>WebSocket: {{ wsConnected ? '已连接' : '断开' }}</span>
    </el-footer>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Fold, UserFilled, DataBoard, Monitor } from '@element-plus/icons-vue'
import { useUserStore } from '../../stores/user'
import { useWebSocket } from '../../composables/useWebSocket'

const userStore = useUserStore()
const router = useRouter()
const route = useRoute()

const collapsed = ref(false)
const onlineCount = ref(0)
const offlineCount = ref(0)

const activeMenu = computed(() => route.path)

const {
  connected: wsConnected,
  send: wsSend,
  on: wsOn,
  off: wsOff,
  connect: wsConnect,
  disconnect: wsDisconnect,
} = useWebSocket()

// WS event handlers
function handleInitialState(msg: { payload: { online_nodes?: string[] } }) {
  const nodes = msg.payload?.online_nodes || []
  onlineCount.value = nodes.length
}

function handleNodeOnline(_msg: { payload: { node_id?: string } }) {
  onlineCount.value++
  offlineCount.value = Math.max(0, offlineCount.value - 1)
}

function handleNodeOffline(_msg: { payload: { node_id?: string } }) {
  onlineCount.value = Math.max(0, onlineCount.value - 1)
  offlineCount.value++
}

onMounted(() => {
  wsConnect()
  wsOn('initial_state', handleInitialState)
  wsOn('node_online', handleNodeOnline)
  wsOn('node_offline', handleNodeOffline)
})

onUnmounted(() => {
  wsOff('initial_state', handleInitialState)
  wsOff('node_online', handleNodeOnline)
  wsOff('node_offline', handleNodeOffline)
})

function handleLogout() {
  userStore.logout()
  wsDisconnect()
  router.push('/login')
}
</script>

<style scoped>
.app-layout { height: 100vh; }
.app-header {
  display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid #e4e7ed;
  padding: 0 16px;
}
.header-left { display: flex; align-items: center; gap: 8px; }
.logo-text { font-size: 18px; font-weight: 600; color: #409eff; }
.header-right { display: flex; align-items: center; gap: 16px; }
.user-dropdown { cursor: pointer; display: flex; align-items: center; gap: 4px; }
.app-sidebar { background: #fafafa; border-right: 1px solid #e4e7ed; overflow: auto; }
.sidebar-menu { border-right: none; }
.app-main { background: #f0f2f5; min-height: 0; }
.app-footer {
  display: flex; align-items: center; gap: 24px;
  background: #fff; border-top: 1px solid #e4e7ed;
  padding: 0 16px; font-size: 13px; color: #909399;
  height: 36px !important;
}
</style>
