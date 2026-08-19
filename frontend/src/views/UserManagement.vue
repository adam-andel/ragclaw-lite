<!--
  Copyright 2026 徐松夏（Xu Songxia）

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton, NTag, NInput, NSelect, NPopconfirm, NSpace, NIcon, NEmpty, NDescriptions, NDescriptionsItem, NSpin, NTooltip } from 'naive-ui'
import { Add, Trash, Eye, People, Ban, CheckmarkCircle, Search } from '@vicons/ionicons5'
import StatusToggle from '@/components/common/StatusToggle.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import AppCard from '@/components/common/AppCard.vue'
import { useI18n } from 'vue-i18n'
import { formatDateTime } from '@/i18n/format'
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
const { t } = useI18n()
const users = ref<UserRow[]>([])
const loading = ref(false)
// ── Server-side pagination: fetch only the current page; total is returned by the backend ──
const page = ref(1)
const pageSize = 20
const total = ref(0)
function onPageChange(p: number) { page.value = p; loadUsers() }

// Filters — search + enable/disable + role (mirrors DocumentManage.vue)
const search = ref('')
const filterActive = ref<'all' | 'active' | 'inactive'>('all')
const activeOptions = [
  { label: t('users.filter.allStatus'), value: 'all' },
  { label: t('common.enabled'), value: 'active' },
  { label: t('common.disabled'), value: 'inactive' },
]
// Role options: both admin and moderator can see all roles (moderator is allowed to "view all users",
// but modify/delete are still constrained by the backend's can_manage_user; the UI uses canManage to grey out dangerous actions)
const filterRole = ref<'all' | 'user' | 'moderator' | 'admin'>('all')
const roleOptions = [
  { label: t('users.filter.allRoles'), value: 'all' },
  { label: t('common.role.regular'), value: 'user' },
  { label: t('common.role.admin'), value: 'moderator' },
  { label: t('common.role.superAdmin'), value: 'admin' },
]

function onSearch() {
  page.value = 1
  loadUsers()
}

function resetFilters() {
  search.value = ''
  filterActive.value = 'all'
  filterRole.value = 'all'
  page.value = 1
  loadUsers()
}

const showCreate = ref(false)
const newUser = ref({ username: '', password: '', display_name: '', role: 'user' })
const creating = ref(false)

const showDetail = ref(false)
const detailUser = ref<UserRow | null>(null)

onMounted(loadUsers)

async function loadUsers() {
  loading.value = true
  try {
    const params: any = { page: page.value, size: pageSize }
    if (search.value) params.search = search.value
    if (filterActive.value !== 'all') params.is_active = filterActive.value === 'active'
    if (filterRole.value !== 'all') params.role = filterRole.value
    const r = await client.get('/users', { params })
    users.value = r.data.items
    total.value = r.data.total
    // After deleting the last item on the last page, the current page may go out of range; fall back to the last valid page and reload
    const totalPages = Math.max(1, Math.ceil(total.value / pageSize))
    if (page.value > totalPages) {
      page.value = totalPages
      params.page = page.value
      const r2 = await client.get('/users', { params })
      users.value = r2.data.items
      total.value = r2.data.total
    }
  } catch { /* noop */ }
  finally { loading.value = false }
}

function roleLabel(role: string) {
  if (role === 'admin') return t('common.role.superAdmin')
  if (role === 'moderator') return t('common.role.admin')
  return t('common.role.user')
}
function roleType(role: string): 'error' | 'warning' | 'info' {
  if (role === 'admin') return 'error'
  if (role === 'moderator') return 'warning'
  return 'info'
}
// Whether the current user can manage the target user: admin can manage everyone, moderator can only manage regular users
// (consistent with the backend's can_manage_user semantics). Used to decide greying out the disable/delete buttons.
function canManage(user: UserRow) {
  return auth.isAdmin || user.role === 'user'
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

function formatTime(value: string) {
  return formatDateTime(value)
}
</script>

<template>
  <div class="page-container pm-flex">
    <PageHeader :title="t('users.pageTitle')" :icon="People">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NButton type="primary" size="small" @click="showCreate = true">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('users.createUser') }}
        </NButton>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" :placeholder="t('users.searchPlaceholder')" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterActive" :options="activeOptions" :placeholder="t('common.status')" size="small" style="width:130px" @update:value="onSearch" />
      <NSelect v-model:value="filterRole" :options="roleOptions" :placeholder="t('users.role')" size="small" style="width:130px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>{{ t('common.reset') }}</NButton>
    </div>

    <NSpin :show="loading" class="pm-scroll">
      <NEmpty v-if="!loading && total === 0" :description="t('users.empty')" />
      <div class="um-list" v-if="users.length > 0">
        <AppCard
          v-for="user in users"
          :key="user.id"
          class="um-card"
          :disabled="!user.is_active"
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
                  {{ user.is_active ? t('users.normal') : t('common.disabled') }}
                </NTag>
              </div>
            </div>
            <div class="um-card-toggle" @click.stop>
              <NTooltip v-if="!canManage(user)" trigger="hover">
                <template #trigger>
                  <StatusToggle
                    :value="user.is_active"
                    :disabled="!canManage(user)"
                    @update:value="() => toggleStatus(user)"
                  />
                </template>
                {{ t('users.noPermissionRole') }}
              </NTooltip>
              <StatusToggle
                v-else
                :value="user.is_active"
                :disabled="!canManage(user)"
                @update:value="() => toggleStatus(user)"
              />
            </div>
          </div>
          <div class="um-card-meta">
            <span class="um-meta-muted">{{ t('common.createdAt') }} {{ formatTime(user.created_at) }}</span>
          </div>
        </AppCard>
      </div>
    </NSpin>

    <AppPagination always-show :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />

    <!-- Create Modal -->
    <AppModal v-model:show="showCreate" :title="t('users.createUser')" size="detail" :title-style="{fontSize:'1.25rem',fontWeight:'bold'}">
      <div class="create-form">
        <NInput v-model:value="newUser.username" :placeholder="t('users.username')" size="large" />
        <NInput v-model:value="newUser.password" type="password" :placeholder="t('users.password')" size="large" />
        <NInput v-model:value="newUser.display_name" :placeholder="t('users.displayNameOptional')" size="large" />
        <NSelect
          v-model:value="newUser.role"
          :options="auth.isAdmin ? [{ label: t('common.role.regular'), value: 'user' }, { label: t('common.role.admin'), value: 'moderator' }, { label: t('common.role.superAdmin'), value: 'admin' }] : [{ label: t('common.role.regular'), value: 'user' }]"
          :placeholder="t('users.role')"
          size="large"
        />
        <NButton type="primary" :loading="creating" @click="createUser" block size="large">{{ t('common.create') }}</NButton>
      </div>
    </AppModal>

    <!-- Detail Modal -->
    <AppModal
      v-model:show="showDetail"
      :title="t('users.detailTitle')"
      size="detail"
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
          <NDescriptionsItem :label="t('users.username')">{{ detailUser.username }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('users.displayName')">{{ detailUser.display_name || '—' }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('users.email')">
            <span v-if="detailUser.email">{{ detailUser.email }}</span>
            <span v-else class="um-meta-muted">—</span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('users.role')">
            <NTag :type="roleType(detailUser.role)" size="small">{{ roleLabel(detailUser.role) }}</NTag>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('common.status')">
            <NTag :type="detailUser.is_active ? 'success' : 'default'" size="small">
              {{ detailUser.is_active ? t('users.normal') : t('common.disabled') }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('common.createdAt')">{{ formatTime(detailUser.created_at) }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('users.tenantId')">
            <span v-if="detailUser.tenant_id" class="um-id">{{ detailUser.tenant_id }}</span>
            <span v-else class="um-meta-muted">—</span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('users.userId')">
            <span class="um-id">{{ detailUser.id }}</span>
          </NDescriptionsItem>
        </NDescriptions>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailUser && auth.isAdmin" @click="viewConversations(detailUser.id)">
            <template #icon><NIcon><Eye /></NIcon></template>
            {{ t('users.viewConversations') }}
          </NButton>
          <NSelect
            v-if="detailUser && auth.isAdmin"
            size="small"
            style="width: 140px"
            :value="detailUser.role"
            :options="[{ label: t('common.role.regular'), value: 'user' }, { label: t('common.role.admin'), value: 'moderator' }, { label: t('common.role.superAdmin'), value: 'admin' }]"
            @update:value="(r: string) => { if (detailUser) setRole(detailUser, r) }"
          />
          <NTooltip v-if="detailUser && !canManage(detailUser)" trigger="hover">
            <template #trigger>
              <NButton size="small" :disabled="!canManage(detailUser)" :style="detailUser.is_active
                ? { '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b', '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706', '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706' }
                : { '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e', '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a', '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a' }">
                <template #icon>
                  <NIcon>
                    <Ban v-if="detailUser.is_active" />
                    <CheckmarkCircle v-else />
                  </NIcon>
                </template>
                {{ detailUser.is_active ? t('common.disable') : t('common.enable') }}
              </NButton>
            </template>
            {{ t('users.noPermissionRole') }}
          </NTooltip>
          <NButton v-else-if="detailUser" size="small" :disabled="!canManage(detailUser)" @click="toggleStatus(detailUser)" :style="detailUser.is_active
            ? { '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b', '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706', '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706' }
            : { '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e', '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a', '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a' }">
            <template #icon>
              <NIcon>
                <Ban v-if="detailUser.is_active" />
                <CheckmarkCircle v-else />
              </NIcon>
            </template>
            {{ detailUser.is_active ? t('common.disable') : t('common.enable') }}
          </NButton>
          <NTooltip v-if="detailUser && !canManage(detailUser)" trigger="hover">
            <template #trigger>
              <NPopconfirm @positive-click="deleteUser(detailUser.id)">
                <template #trigger>
                  <NButton size="small" :disabled="!canManage(detailUser)" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                    <template #icon><NIcon><Trash /></NIcon></template>
                    {{ t('common.delete') }}
                  </NButton>
                </template>
                {{ t('users.confirmDeleteUser') }}
              </NPopconfirm>
            </template>
            {{ t('users.noPermissionRole') }}
          </NTooltip>
          <NPopconfirm v-else-if="detailUser" @positive-click="deleteUser(detailUser.id)">
            <template #trigger>
              <NButton size="small" :disabled="!canManage(detailUser)" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                <template #icon><NIcon><Trash /></NIcon></template>
                {{ t('common.delete') }}
              </NButton>
            </template>
            {{ t('users.confirmDeleteUser') }}
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
/* User card grid */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.um-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  padding-top: 2px; /* prevent hover border-top clipping from overflow:auto parent */
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

.create-form { display: flex; flex-direction: column; gap: 14px; padding: 20px 24px; background: var(--color-surface, #fff); border-radius: 12px; height: 100%; box-sizing: border-box; }
/* Sticky footer pagination: the list scrolls, the pager stays pinned at the
   bottom of the viewport (mirrors WorkspaceView.vue). */
.pm-flex {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.pm-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>
