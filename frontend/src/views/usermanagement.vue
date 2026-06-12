<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NCard, NButton, NTag, NModal, NInput, NSelect, NPopconfirm, NSpace, NIcon, NDataTable, NEmpty } from 'naive-ui'
import { Add, Trash, Eye } from '@vicons/ionicons5'
import client from '@/api/client'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

interface UserRow {
  id: string
  username: string
  display_name: string
  email: string | null
  role: string
  is_active: boolean
  tenant_id: string | null
  created_at: string
}

const auth = useAuthStore()
const router = useRouter()
const users = ref<UserRow[]>([])
const loading = ref(false)

const showCreate = ref(false)
const newUser = ref({ username: '', password: '', display_name: '', role: 'user' })
const creating = ref(false)

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  try { const r = await client.get('/users'); users.value = r.data } catch { /* noop */ }
  loading.value = false
}

async function createUser() {
  if (!newUser.value.username || !newUser.value.password) return
  creating.value = true
  try {
    await client.post('/users', newUser.value)
    showCreate.value = false
    newUser.value = { username: '', password: '', display_name: '', role: 'user' }
    await loadUsers()
  } catch (e: any) { console.error(e.message) }
  finally { creating.value = false }
}

async function deleteUser(id: string) {
  try { await client.delete(`/users/${id}`); await loadUsers() } catch (e: any) { console.error(e.message) }
}

function viewConversations(userId: string) {
  router.push({ path: '/chat', query: { view_user: userId } })
}

async function toggleStatus(user: UserRow) {
  try { await client.put(`/users/${user.id}`, { is_active: !user.is_active }); await loadUsers() } catch { /* noop */ }
}

async function toggleRole(user: UserRow) {
  const newRole = user.role === 'admin' ? 'user' : 'admin'
  try { await client.put(`/users/${user.id}`, { role: newRole }); await loadUsers() } catch { /* noop */ }
}

const columns = [
  { title: '用户名', key: 'username', width: 130 },
  { title: '显示名', key: 'display_name', width: 130 },
  { title: '邮箱', key: 'email', width: 180, render: (r: UserRow) => r.email || '-' },
  {
    title: '角色', key: 'role', width: 80,
    render: (r: UserRow) => h(NTag, { type: r.role === 'admin' ? 'error' : 'info', size: 'small' as const }, { default: () => r.role === 'admin' ? '管理员' : '用户' }),
  },
  {
    title: '状态', key: 'is_active', width: 80,
    render: (r: UserRow) => h(NTag, { type: r.is_active ? 'success' : 'default', size: 'small' as const }, { default: () => r.is_active ? '正常' : '已禁用' }),
  },
  { title: '创建时间', key: 'created_at', width: 170, render: (r: UserRow) => new Date(r.created_at).toLocaleString('zh-CN') },
  {
    title: '操作', key: 'actions', width: 280,
    render: (r: UserRow) => {
      if (r.id === auth.user?.id) return h('span', { style: 'color: var(--color-text-muted)' }, '当前用户')
      return h(NSpace, { size: 'small' }, {
        default: () => [
          h(NButton, { text: true, size: 'tiny', onClick: () => viewConversations(r.id) }, { default: () => h(NIcon, null, { default: () => h(Eye) }) }),
          h(NButton, { text: true, size: 'tiny', type: 'warning', onClick: () => toggleRole(r) }, { default: () => r.role === 'admin' ? '降为用户' : '升管理员' }),
          h(NButton, { text: true, size: 'tiny', onClick: () => toggleStatus(r) }, { default: () => r.is_active ? '禁用' : '启用' }),
          h(NPopconfirm, { onPositiveClick: () => deleteUser(r.id) }, {
            trigger: () => h(NButton, { text: true, size: 'tiny', type: 'error' }, { default: () => h(NIcon, null, { default: () => h(Trash) }) }),
            default: () => '确定删除该用户？',
          }),
        ],
      })
    },
  },
]
</script>
<script lang="ts">
import { h } from 'vue'
</script>

<template>
  <div class="user-view">
    <div class="header">
      <h2>👥 用户管理</h2>
      <NButton type="primary" size="small" @click="showCreate = true">
        <template #icon><NIcon><Add /></NIcon></template>
        新建用户
      </NButton>
    </div>

    <NDataTable
      :columns="columns"
      :data="users"
      :loading="loading"
      :bordered="false"
      size="small"
    >
      <template #empty>
        <NEmpty description="暂无用户" />
      </template>
    </NDataTable>

    <NModal v-model:show="showCreate" title="新建用户">
      <div class="form">
        <NInput v-model:value="newUser.username" placeholder="用户名" />
        <NInput v-model:value="newUser.password" type="password" placeholder="密码" />
        <NInput v-model:value="newUser.display_name" placeholder="显示名称（可选）" />
        <NSelect
          v-model:value="newUser.role"
          :options="[{ label: '普通用户', value: 'user' }, { label: '管理员', value: 'admin' }]"
          placeholder="角色"
        />
        <NButton type="primary" :loading="creating" @click="createUser" block>创建</NButton>
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.user-view { max-width: 1000px; margin: 0 auto; }
.header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 20px;
}
.header h2 { font-size: 1.25rem; }
.form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; min-width: 350px; }
</style>
