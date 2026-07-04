import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
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
      meta: { title: '对话', requiresAuth: true },
    },
    {
      path: '/chat/:id',
      name: 'chat-conversation',
      component: () => import('@/views/ChatView.vue'),
      meta: { title: '对话', requiresAuth: true },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('@/views/KnowledgeBase.vue'),
      meta: { title: '知识库', requiresAuth: true },
    },
    {
      path: '/documents',
      name: 'documents',
      component: () => import('@/views/DocumentManage.vue'),
      meta: { title: '文档管理', requiresAuth: true },
    },
    {
      path: '/debug',
      redirect: () => ({ path: '/settings', hash: '#retrieval' }),
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('@/views/Dashboard.vue'),
      meta: { title: '仪表盘', requiresAuth: true },
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('@/views/ProfileView.vue'),
      meta: { title: '个人信息', requiresAuth: true },
    },
    {
      path: '/users',
      name: 'users',
      component: () => import('@/views/UserManagement.vue'),
      meta: { title: '用户管理', requiresAuth: true, admin: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { title: '系统设置', requiresAuth: true, admin: true },
    },
    {
      path: '/plugins',
      redirect: () => ({ path: '/settings', hash: '#plugins' }),
    },
    {
      path: '/skills',
      name: 'skills',
      component: () => import('@/views/SkillsView.vue'),
      meta: { title: '技能管理', requiresAuth: true },
    },
    {
      path: '/mcp',
      name: 'mcp',
      component: () => import('@/views/McpServersView.vue'),
      meta: { title: 'MCP 服务', requiresAuth: true },
    },
    {
      path: '/cron-jobs',
      name: 'cron-jobs',
      component: () => import('@/views/CronJobsView.vue'),
      meta: { title: '定时任务', requiresAuth: true },
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('@/views/NotificationsView.vue'),
      meta: { title: '通知中心', requiresAuth: true },
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
  } else if (!auth.isStaff && to.path !== '/chat' && !to.path.startsWith('/chat') && to.path !== '/login' && to.path !== '/profile' && to.path !== '/notifications') {
    next('/chat')
  } else if (to.meta.guest && auth.isLoggedIn) {
    next('/chat')
  } else {
    next()
  }
})

export default router
