<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NCard, NButton, NTag, NModal, NInput, NSelect, NPopconfirm, NSpace, NIcon, NEmpty, NDescriptions, NDescriptionsItem, NSwitch } from 'naive-ui'
import { Add, Trash, Eye, People, Ban, CheckmarkCircle } from '@vicons/ionicons5'
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
  avatar_url: string | null
  created_at: string
}

const auth = useAuthStore()
const router = useRouter()
const users = ref<UserRow[]>([])
const loading = ref(false)

const showCreate = ref(false)
const newUser = ref({ username: '', password: '', display_name: '', role: 'user' })
const creating = ref(false)

const showDetail = ref(false)
const detailUser = ref<UserRow | null>(null)

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  try { const r = await client.get('/users'); users.value = r.data } catch { /* noop */ }
  finally { loading.value = false }
}

function roleLabel(role: string) {
  if (role === 'admin') return '超级管理员'
  if (role === 'moderator') return '普通管理员'
  return '用户'
}
function roleType(role: string): 'error' | 'warning' | 'info' {
  if (role === 'admin') return 'error'
  if (role === 'moderator') return 'warning'
  return 'info'
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
  try {
    await client.put(`/users/${user.id}`, { is_active: !user.is_active })
    await loadUsers()
    if (detailUser.value && detailUser.value.id === user.id) detailUser.value = { ...detailUser.value, is_active: !user.is_active }
  } catch { /* noop */ }
}

async function setRole(user: UserRow, newRole: string) {
  if (newRole === user.role) return
  try {
    await client.put(`/users/${user.id}`, { role: newRole })
    await loadUsers()
    if (detailUser.value && detailUser.value.id === user.id) detailUser.value = { ...detailUser.value, role: newRole }
  } catch { /* noop */ }
}

function openDetail(user: UserRow) {
  detailUser.value = user
  showDetail.value = true
}

function formatTime(t: string) {
  return new Date(t).toLocaleString('zh-CN')
}
</script>

<template>
  <div class="page-container">
    <div class="dm-header">
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><People /></NIcon>
        <h2>用户管理</h2>
        <span v-if="users.length > 0" class="kb-header-badge">{{ users.length }}</span>
      </div>
      <div class="dm-header-actions">
        <NButton type="primary" size="small" @click="showCreate = true">
          <template #icon><NIcon><Add /></NIcon></template>
          新建用户
        </NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && users.length === 0" description="暂无用户" />
      <div class="um-list" v-if="users.length > 0">
        <NCard
          v-for="user in users"
          :key="user.id"
          size="small"
          :class="['um-card', { 'um-card-disabled': !user.is_active }]"
          hoverable
          role="button"
          tabindex="0"
          @click="openDetail(user)"
          @keydown.enter.prevent="openDetail(user)"
          @keydown.space.prevent="openDetail(user)"
        >
          <div class="um-card-header">
            <div
              class="um-avatar"
              :style="user.avatar_url
                ? { backgroundImage: `url(${user.avatar_url})`, backgroundSize: 'cover', backgroundPosition: 'center', color: 'transparent' }
                : {}"
            >{{ (user.display_name || user.username || '?').charAt(0).toUpperCase() }}</div>
            <div class="um-card-info">
              <div class="um-card-info-top">
                <span class="um-name" :title="user.username">{{ user.username }}</span>
                <span class="um-display-name" :title="user.display_name">{{ user.display_name }}</span>
              </div>
              <div class="um-card-info-bottom">
                <NTag size="small" :type="roleType(user.role)" :bordered="false">{{ roleLabel(user.role) }}</NTag>
                <NTag size="small" :type="user.is_active ? 'success' : 'default'" :bordered="false">
                  {{ user.is_active ? '正常' : '已禁用' }}
                </NTag>
              </div>
            </div>
            <div class="um-card-toggle" @click.stop>
              <NSwitch
                size="small"
                :value="user.is_active"
                :disabled="user.id === auth.user?.id"
                @update:value="() => toggleStatus(user)"
              >
                <template #checked>启用</template>
                <template #unchecked>禁用</template>
              </NSwitch>
            </div>
          </div>
          <div class="um-card-meta">
            <span class="um-meta-muted">创建时间 {{ formatTime(user.created_at) }}</span>
          </div>
        </NCard>
      </div>
    </NSpin>

    <!-- Create Modal -->
    <NModal v-model:show="showCreate" title="新建用户" style="width:70vw; max-width:600px; height:70vh; max-height:460px" :title-style="{fontSize:'1.25rem',fontWeight:'bold'}">
      <div class="create-form">
        <NInput v-model:value="newUser.username" placeholder="用户名" size="large" />
        <NInput v-model:value="newUser.password" type="password" placeholder="密码" size="large" />
        <NInput v-model:value="newUser.display_name" placeholder="显示名称（可选）" size="large" />
        <NSelect
          v-model:value="newUser.role"
          :options="auth.isAdmin ? [{ label: '普通用户', value: 'user' }, { label: '普通管理员', value: 'moderator' }, { label: '超级管理员', value: 'admin' }] : [{ label: '普通用户', value: 'user' }]"
          placeholder="角色"
          size="large"
        />
        <NButton type="primary" :loading="creating" @click="createUser" block size="large">创建</NButton>
      </div>
    </NModal>

    <!-- Detail Modal -->
    <NModal
      v-model:show="showDetail"
      preset="card"
      :title="detailUser?.username || '用户详情'"
      style="width: 90vw; max-width: 560px"
    >
      <NSpin :show="loading">
        <NDescriptions
          v-if="detailUser"
          bordered
          :column="1"
          size="small"
          label-placement="left"
          label-style="width: 110px"
        >
          <NDescriptionsItem label="用户名">{{ detailUser.username }}</NDescriptionsItem>
          <NDescriptionsItem label="显示名">{{ detailUser.display_name || '—' }}</NDescriptionsItem>
          <NDescriptionsItem label="邮箱">
            <span v-if="detailUser.email">{{ detailUser.email }}</span>
            <span v-else class="um-meta-muted">—</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="角色">
            <NTag :type="roleType(detailUser.role)" size="small">{{ roleLabel(detailUser.role) }}</NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="状态">
            <NTag :type="detailUser.is_active ? 'success' : 'default'" size="small">
              {{ detailUser.is_active ? '正常' : '已禁用' }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="创建时间">{{ formatTime(detailUser.created_at) }}</NDescriptionsItem>
          <NDescriptionsItem label="租户 ID">
            <span v-if="detailUser.tenant_id" class="um-id">{{ detailUser.tenant_id }}</span>
            <span v-else class="um-meta-muted">—</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="用户 ID">
            <span class="um-id">{{ detailUser.id }}</span>
          </NDescriptionsItem>
        </NDescriptions>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailUser" @click="viewConversations(detailUser.id)">
            <template #icon><NIcon><Eye /></NIcon></template>
            查看对话
          </NButton>
          <NSelect
            v-if="detailUser && auth.isAdmin"
            size="small"
            style="width: 140px"
            :value="detailUser.role"
            :options="[{ label: '普通用户', value: 'user' }, { label: '普通管理员', value: 'moderator' }, { label: '超级管理员', value: 'admin' }]"
            @update:value="(r: string) => { if (detailUser) setRole(detailUser, r) }"
          />
          <NButton size="small" v-if="detailUser" :disabled="detailUser.id === auth.user?.id" @click="toggleStatus(detailUser)" :style="detailUser.is_active
            ? { '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b', '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706', '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706' }
            : { '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e', '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a', '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a' }">
            <template #icon>
              <NIcon>
                <Ban v-if="detailUser.is_active" />
                <CheckmarkCircle v-else />
              </NIcon>
            </template>
            {{ detailUser.is_active ? '禁用' : '启用' }}
          </NButton>
          <NPopconfirm v-if="detailUser" @positive-click="deleteUser(detailUser.id)">
            <template #trigger>
              <NButton size="small" :disabled="detailUser.id === auth.user?.id" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                <template #icon><NIcon><Trash /></NIcon></template>
                删除
              </NButton>
            </template>
            确定删除该用户？
          </NPopconfirm>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.page-container {
  padding: var(--space-4);
  max-width: 1100px;
  margin: 0 auto;
}
.dm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding: 16px 20px;
  background: linear-gradient(135deg, var(--color-primary-soft), transparent);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}
.dm-header .kb-header-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.dm-header .kb-header-title h2 {
  font-size: var(--text-xl);
  font-weight: 700;
  margin: 0;
}
.dm-header .kb-header-badge {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-soft);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-primary);
}
.dm-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* User card grid */
.um-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.um-card {
  cursor: pointer;
  transition: box-shadow .2s, border-color .2s;
}
.um-card:hover {
  box-shadow: var(--shadow-sm);
}
.um-card:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -1px;
  border-radius: var(--radius);
}
.um-card-disabled {
  background: #f1f5f9;
}
.um-card-disabled:hover {
  box-shadow: var(--shadow-sm);
}
html.dark .um-card-disabled {
  background: #334155;
}

.um-card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.um-avatar {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
  font-size: var(--text-lg);
}
.um-card-info {
  flex: 1;
  min-width: 0;
}
.um-card-info-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.um-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 120px;
}
.um-display-name {
  font-size: var(--text-xs);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 90px;
}
.um-card-info-bottom {
  display: flex;
  align-items: center;
}
.um-card-toggle {
  flex-shrink: 0;
}

.um-card-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.um-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  flex-wrap: wrap;
}
.um-meta-muted {
  color: var(--color-text);
}
.um-id {
  font-family: monospace;
  font-size: 12px;
  word-break: break-all;
}

.create-form { display: flex; flex-direction: column; gap: 14px; padding: 20px 24px; background: #fff; border-radius: 12px; height: 100%; box-sizing: border-box; }
</style>
