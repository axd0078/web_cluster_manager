import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'
import Login from '../views/Login.vue'
import Dashboard from '../views/Dashboard.vue'
import Nodes from '../views/Nodes.vue'
import AppLayout from '../components/layout/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'Login',
      component: Login,
      meta: { requiresGuest: true },
    },
    {
      path: '/',
      component: AppLayout,
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'Dashboard', component: Dashboard },
        { path: 'nodes', name: 'Nodes', component: Nodes },
      ],
    },
  ],
})

router.beforeEach((to, _from, next) => {
  const userStore = useUserStore()
  if (to.meta.requiresAuth && !userStore.token) {
    next('/login')
  } else if (to.meta.requiresGuest && userStore.token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
