import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('../components/layout/AppLayout.vue'),
      children: [
        {
          path: '',
          name: 'Dashboard',
          component: () => import('../views/Dashboard.vue'),
        },
        {
          path: 'nodes',
          name: 'Nodes',
          component: () => import('../views/Nodes.vue'),
        },
        {
          path: 'groups',
          name: 'Groups',
          component: () => import('../views/Groups.vue'),
        },
        {
          path: 'tasks',
          name: 'Tasks',
          component: () => import('../views/Tasks.vue'),
          meta: { permission: 'tasks.low' },
        },
        {
          path: 'files',
          name: 'FileTransfer',
          component: () => import('../views/FileTransfer.vue'),
          meta: { permission: 'files.write_sandbox' },
        },
        {
          path: 'terminal',
          name: 'Terminal',
          component: () => import('../views/Terminal.vue'),
          meta: { permission: 'terminal.low' },
        },
        {
          path: 'monitor',
          name: 'Monitor',
          component: () => import('../views/MonitorScreen.vue'),
        },
        {
          path: 'updates',
          name: 'Updates',
          component: () => import('../views/Updates.vue'),
          meta: { permission: 'updates.read' },
        },
        {
          path: 'audit',
          name: 'AuditLogs',
          component: () => import('../views/AuditLogs.vue'),
          meta: { permission: 'audit.read' },
        },
        {
          path: 'settings',
          name: 'Settings',
          component: () => import('../views/Settings.vue'),
          meta: { permission: 'settings.read' },
        },
      ],
    },
  ],
})

router.beforeEach(async to => {
  const user = useUserStore()
  await user.fetchUser()
  if (to.meta.public) return user.isAuthenticated ? '/' : true
  if (!user.isAuthenticated) return '/login'
  const permission = to.meta.permission as string | undefined
  if (permission && !user.hasPermission(permission)) return '/'
  return true
})

export default router
