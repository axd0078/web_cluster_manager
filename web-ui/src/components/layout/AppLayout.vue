<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Bell,
  DataAnalysis,
  Expand,
  Fold,
  FolderOpened,
  Grid,
  HomeFilled,
  Monitor,
  Moon,
  Operation,
  Promotion,
  Setting,
  Sunny,
  SwitchButton,
  Upload,
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
  { path: '/', title: '仪表盘', icon: HomeFilled, permission: 'cluster.read' },
  { path: '/nodes', title: '节点管理', icon: Monitor, permission: 'cluster.read' },
  { path: '/groups', title: '分组管理', icon: Grid, permission: 'cluster.read' },
  { path: '/tasks', title: '任务中心', icon: Operation, permission: 'tasks.low' },
  { path: '/files', title: '文件传输', icon: FolderOpened, permission: 'files.write_sandbox' },
  { path: '/terminal', title: '安全终端', icon: Promotion, permission: 'terminal.low' },
  { path: '/monitor', title: '监控大屏', icon: DataAnalysis, permission: 'cluster.read' },
  { path: '/updates', title: '更新管理', icon: Upload, permission: 'updates.read' },
  { path: '/audit', title: '审计日志', icon: Operation, permission: 'audit.read' },
  { path: '/settings', title: '系统设置', icon: Setting, permission: 'settings.read' },
]

const menuItems = computed(() =>
  allMenuItems.filter(item => userStore.hasPermission(item.permission)),
)
const activeMenu = computed(() =>
  route.path === '/' ? '/' : `/${route.path.split('/')[1]}`,
)

function toggleTheme() {
  document.documentElement.classList.toggle('dark', isDark.value)
}

async function handleLogout() {
  await userStore.logout()
  await router.push('/login')
}

async function handleStepUp() {
  try {
    const result = await ElMessageBox.prompt(
      '请输入当前管理员密码。高权限授权仅绑定本次登录，有效 5 分钟。',
      '管理员二次认证',
      { inputType: 'password', inputPattern: /.+/, inputErrorMessage: '请输入密码' },
    )
    await userStore.stepUp(result.value)
    ElMessage.success('管理员高权限授权已生效')
  } catch {
    // Cancelling the prompt isn't an application error.
  }
}

async function revokeStepUp() {
  await userStore.revokeStepUp()
  ElMessage.success('管理员高权限授权已撤销')
}

let statsTimer: ReturnType<typeof setInterval> | undefined
watch(() => route.path, () => {
  nodeStore.fetchHealth()
  if (statsTimer) clearInterval(statsTimer)
  statsTimer = setInterval(() => nodeStore.fetchHealth(), 15000)
}, { immediate: true })
onBeforeUnmount(() => {
  if (statsTimer) clearInterval(statsTimer)
})
</script>

<template>
  <el-container class="app-layout">
    <el-aside :width="isCollapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo">
        <span v-if="!isCollapsed">Web 集群管理</span>
        <span v-else>WCM</span>
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
          <el-badge
            :value="nodeStore.health?.alerts_active || 0"
            :hidden="!nodeStore.health?.alerts_active"
          >
            <el-icon :size="20"><Bell /></el-icon>
          </el-badge>
          <el-tag :type="userStore.isAdmin ? 'danger' : 'info'" effect="plain">
            {{ userStore.isAdmin ? '管理员' : '普通用户' }}
          </el-tag>
          <el-dropdown>
            <span>{{ userStore.user?.username || '用户' }}</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-if="userStore.isAdmin && !userStore.hasPermission('terminal.admin')"
                  @click="handleStepUp"
                >
                  管理员授权（5 分钟）
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="userStore.hasPermission('terminal.admin')"
                  @click="revokeStepUp"
                >
                  撤销高权限授权
                </el-dropdown-item>
                <el-dropdown-item @click="handleLogout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </el-space>
      </el-header>

      <el-main class="main-content">
        <router-view />
      </el-main>

      <el-footer class="statusbar">
        <el-space :size="24">
          <span>在线：{{ nodeStore.health?.online_nodes ?? 0 }}</span>
          <span>离线：{{ nodeStore.health?.offline_nodes ?? 0 }}</span>
          <span v-if="nodeStore.health?.avg_cpu !== null">
            CPU：{{ nodeStore.health?.avg_cpu?.toFixed(1) }}%
          </span>
          <span v-if="nodeStore.health?.avg_memory !== null">
            内存：{{ nodeStore.health?.avg_memory?.toFixed(1) }}%
          </span>
          <span v-if="nodeStore.health?.avg_disk !== null">
            磁盘：{{ nodeStore.health?.avg_disk?.toFixed(1) }}%
          </span>
        </el-space>
        <span class="muted">健康评分：{{ nodeStore.health?.health_score ?? '—' }} / 100</span>
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
.muted { color: var(--el-text-color-secondary); }
</style>
