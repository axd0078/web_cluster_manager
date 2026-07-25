<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Monitor, DataAnalysis, Grid, FolderOpened, Upload, Promotion,
  Operation, Setting, HomeFilled, SwitchButton, Sunny, Moon,
  Expand, Fold, Bell,
} from '@element-plus/icons-vue'
import { useUserStore } from '../../stores/user'
import { useNodeStore } from '../../stores/nodes'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const nodeStore = useNodeStore()
const isDark = ref(false)
const isCollapsed = ref(false)

const allMenuItems = [
  { path: '/', title: '仪表盘', icon: HomeFilled },
  { path: '/nodes', title: '节点管理', icon: Monitor },
  { path: '/groups', title: '分组管理', icon: Grid },
  { path: '/tasks', title: '任务中心', icon: Operation },
  { path: '/files', title: '文件传输', icon: FolderOpened },
  { path: '/terminal', title: '远程终端', icon: Promotion },
  { path: '/monitor', title: '监控大屏', icon: DataAnalysis },
  { path: '/updates', title: '更新管理', icon: Upload },
  { path: '/audit', title: '审计日志', icon: Operation },
  { path: '/settings', title: '系统设置', icon: Setting },
]
const menuItems = computed(() => allMenuItems.filter(item => (
  item.path !== '/tasks' || userStore.user?.role !== 'viewer'
)))

const activeMenu = computed(() => {
  if (route.path === '/') return '/'
  return '/' + route.path.split('/')[1]
})

function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
}

async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}

// Refresh stats periodically
let statsTimer: ReturnType<typeof setInterval>
watch(() => route.path, () => {
  nodeStore.fetchHealth()
  clearInterval(statsTimer)
  statsTimer = setInterval(() => nodeStore.fetchHealth(), 15000)
}, { immediate: true })
</script>

<template>
  <el-container class="app-layout">
    <!-- Sidebar -->
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo">
        <span v-if="!isCollapsed">⚡ 集群管理</span>
        <span v-else>⚡</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        router
        background-color="transparent"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- Topbar -->
      <el-header class="topbar">
        <el-button text @click="isCollapsed = !isCollapsed">
            <el-icon><component :is="isCollapsed ? Expand : Fold" /></el-icon>
          </el-button>
        <el-space>
          <el-switch
            v-model="isDark"
            :active-icon="Moon"
            :inactive-icon="Sunny"
            @change="toggleTheme"
          />
          <el-badge :value="nodeStore.health?.alerts_active || 0" :hidden="!nodeStore.health?.alerts_active">
            <el-icon :size="20"><Bell /></el-icon>
          </el-badge>
          <el-dropdown>
            <el-space>
              <el-avatar :size="28" icon="UserFilled" />
              <span>{{ userStore.user?.username || 'Admin' }}</span>
            </el-space>
            <template #dropdown>
              <el-dropdown-item @click="handleLogout">
                <el-icon><SwitchButton /></el-icon> 退出登录
              </el-dropdown-item>
            </template>
          </el-dropdown>
        </el-space>
      </el-header>

      <!-- Main Content -->
      <el-main class="main-content">
        <router-view />
      </el-main>

      <!-- Status Bar -->
      <el-footer class="statusbar">
        <el-space :size="24">
          <span>🟢 在线: {{ nodeStore.health?.online_nodes ?? 0 }}</span>
          <span>🔴 离线: {{ nodeStore.health?.offline_nodes ?? 0 }}</span>
          <span v-if="nodeStore.health?.avg_cpu !== null">⚡ CPU: {{ nodeStore.health?.avg_cpu?.toFixed(1) }}%</span>
          <span v-if="nodeStore.health?.avg_memory !== null">💾 内存: {{ nodeStore.health?.avg_memory?.toFixed(1) }}%</span>
          <span v-if="nodeStore.health?.avg_disk !== null">📀 磁盘: {{ nodeStore.health?.avg_disk?.toFixed(1) }}%</span>
        </el-space>
        <span style="color: var(--el-text-color-secondary)">
          健康评分: {{ nodeStore.health?.health_score ?? '—' }} / 100
        </span>
      </el-footer>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-layout { height: 100vh; }

.sidebar {
  background: var(--el-menu-bg-color);
  border-right: 1px solid var(--el-border-color-light);
  transition: width 0.3s;
  overflow: hidden;
}
.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: var(--el-color-primary);
  border-bottom: 1px solid var(--el-border-color-light);
  white-space: nowrap;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid var(--el-border-color-light);
  height: 48px;
}

.main-content {
  background: var(--el-bg-color-page);
  padding: 20px;
  overflow-y: auto;
  height: calc(100vh - 48px - 36px);
}

.statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 16px;
  font-size: 13px;
  border-top: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}
</style>
