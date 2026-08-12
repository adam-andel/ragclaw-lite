import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { i18n } from '@/i18n'
import LoginView from '@/views/LoginView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { guest: true },
    },
    {
      path: '/',
      redirect: '/chat',
    },
    {
      path: '/chat',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      meta: { titleKey: 'nav.chat', requiresAuth: true, keepAlive: true },
    },
    {
      path: '/chat/:id',
      name: 'chat-conversation',
      component: () => import('@/views/ChatView.vue'),
      meta: { titleKey: 'nav.chat', requiresAuth: true, keepAlive: true },
    },
    {
      path: '/knowledge',
      redirect: '/documents',
    },
    {
      path: '/documents',
      name: 'documents',
      component: () => import('@/views/DocumentManage.vue'),
      meta: { titleKey: 'nav.documents', requiresAuth: true },
    },
    {
      path: '/debug',
      redirect: () => ({ path: '/settings', hash: '#retrieval' }),
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('@/views/Dashboard.vue'),
      meta: { titleKey: 'nav.dashboard', requiresAuth: true, admin: true },
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('@/views/ProfileView.vue'),
      meta: { titleKey: 'nav.profile', requiresAuth: true },
    },
    {
      path: '/users',
      name: 'users',
      component: () => import('@/views/UserManagement.vue'),
      meta: { titleKey: 'nav.users', requiresAuth: true, staff: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { titleKey: 'nav.settings', requiresAuth: true, admin: true },
    },
    {
      path: '/plugins',
      redirect: () => ({ path: '/settings', hash: '#plugins' }),
    },
    {
      path: '/skills',
      name: 'skills',
      component: () => import('@/views/SkillsView.vue'),
      meta: { titleKey: 'nav.skills', requiresAuth: true, admin: true },
    },
    {
      path: '/mcp',
      name: 'mcp',
      component: () => import('@/views/McpServersView.vue'),
      meta: { titleKey: 'nav.mcp', requiresAuth: true, admin: true },
    },
    {
      path: '/cron-jobs',
      name: 'cron-jobs',
      component: () => import('@/views/CronJobsView.vue'),
      meta: { titleKey: 'nav.cron', requiresAuth: true },
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
      meta: { titleKey: 'nav.notificationCenter', requiresAuth: true },
    },
    {
      path: '/workspace',
      name: 'workspace',
      component: () => import('@/views/WorkspaceView.vue'),
      meta: { titleKey: 'nav.workspace', requiresAuth: true },
    },
  ],
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()

  if (auth.token && !auth.user) await auth.fetchMe()

  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    next('/login')
  } else if (to.meta.admin && !auth.isAdmin) {
    next('/chat')
  } else if (to.meta.staff && !auth.isStaff) {
    next('/chat')
  } else if (!auth.isStaff && to.path !== '/chat' && !to.path.startsWith('/chat') && to.path !== '/login' && to.path !== '/profile' && to.path !== '/notifications' && !to.path.startsWith('/workspace') && !to.path.startsWith('/documents') && !to.path.startsWith('/cron-jobs')) {
    next('/chat')
  } else if (to.meta.guest && auth.isLoggedIn) {
    next('/chat')
  } else {
    next()
  }
})

router.afterEach((to) => {
  const key = (to.meta.titleKey as string) || 'common.appName'
  document.title = i18n.global.t(key)
})

export default router
