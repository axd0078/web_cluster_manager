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
          meta: { roles: ['admin', 'operator'] },
        },
        {
          path: 'files',
          name: 'FileTransfer',
          component: () => import('../views/FileTransfer.vue'),
        },
        {
          path: 'terminal',
          name: 'Terminal',
          component: () => import('../views/Terminal.vue'),
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
        },
        {
          path: 'audit',
          name: 'AuditLogs',
          component: () => import('../views/AuditLogs.vue'),
        },
        {
          path: 'settings',
          name: 'Settings',
          component: () => import('../views/Settings.vue'),
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
  const roles = to.meta.roles as string[] | undefined
  if (roles && (!user.user || !roles.includes(user.user.role))) return '/'
  return true
})

export default router
