<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  NButton, NForm, NFormItem, NInput,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NTag, NSelect, NInputNumber, NText,
  NEmpty, NSpin,
} from 'naive-ui'
import { Add, Trash, Create, Flash, Refresh, Search } from '@vicons/ionicons5'
import { useI18n } from 'vue-i18n'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusToggle from '@/components/common/StatusToggle.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  listServers, createServer, updateServer, deleteServer, testServer, refreshTools,
} from '@/api/mcp'
import type { MCPServer, MCPServerCreatePayload } from '@/types'

const message = useMessage()
const { t } = useI18n()

// ── Data ──

const servers = ref<MCPServer[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20

// Filters — search + enable/disable (mirrors DocumentManage.vue)
const search = ref('')
const filterActive = ref<'all' | 'active' | 'inactive'>('all')
const activeOptions = [
  { label: t('mcp.allStatus'), value: 'all' },
  { label: t('common.enabled'), value: 'active' },
  { label: t('common.disabled'), value: 'inactive' },
]

const showModal = ref(false)
const editing = ref<MCPServer | null>(null)
const form = ref<MCPServerCreatePayload & { id?: string }>({
  name: '', transport_type: 'http', endpoint: '', command: '', args_json: '', env_json: '',
  timeout_seconds: 30, is_active: true,
})

const testingId = ref('')
const testResult = ref<{ ok?: boolean; message?: string; error?: string; tools?: any[] } | null>(null)
const showTestModal = ref(false)
const testServerName = ref('')
const testModalTitle = computed(() =>
  `${testServerName.value} · ${testResult.value?.ok ? '✅ ' + t('mcp.connectionSuccess') : '❌ ' + t('mcp.connectionFailed')}`
)

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listServers(
      page.value, pageSize,
      search.value || undefined,
      filterActive.value === 'all' ? undefined : filterActive.value === 'active',
    )
    servers.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || t('mcp.loadFailed'))
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  load()
}

function onSearch() {
  page.value = 1
  load()
}

function resetFilters() {
  search.value = ''
  filterActive.value = 'all'
  page.value = 1
  load()
}

onMounted(load)

// ── CRUD ──

function openCreate() {
  editing.value = null
  form.value = { name: '', transport_type: 'http', endpoint: '', command: '', args_json: '', env_json: '', timeout_seconds: 30, is_active: true }
  showModal.value = true
}

function openEdit(server: MCPServer) {
  editing.value = server
  form.value = {
    id: server.id,
    name: server.name,
    transport_type: server.transport_type,
    endpoint: server.endpoint || '',
    command: server.command || '',
    args_json: server.args_json || '',
    env_json: server.env_json || '',
    timeout_seconds: server.timeout_seconds,
    is_active: server.is_active,
  }
  showModal.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await updateServer(editing.value.id, {
        name: form.value.name, transport_type: form.value.transport_type,
        endpoint: form.value.transport_type === 'http' ? form.value.endpoint : undefined,
        command: form.value.transport_type === 'stdio' ? form.value.command : undefined,
        args_json: form.value.args_json || undefined,
        env_json: form.value.env_json || undefined,
        timeout_seconds: form.value.timeout_seconds,
        is_active: form.value.is_active,
      })
      message.success(t('mcp.updated'))
    } else {
      await createServer({
        name: form.value.name, transport_type: form.value.transport_type,
        endpoint: form.value.transport_type === 'http' ? form.value.endpoint : undefined,
        command: form.value.transport_type === 'stdio' ? form.value.command : undefined,
        args_json: form.value.args_json || undefined,
        env_json: form.value.env_json || undefined,
        timeout_seconds: form.value.timeout_seconds,
        is_active: form.value.is_active,
      })
      message.success(t('mcp.created'))
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || t('mcp.saveFailed'))
  }
}

async function handleDelete(server: MCPServer) {
  try {
    await deleteServer(server.id)
    message.success(t('mcp.deleted'))
    await load()
  } catch (e: any) {
    message.error(e.message || t('mcp.deleteFailed'))
  }
}

// ── Enable / Disable ──

async function handleToggle(server: MCPServer) {
  try {
    await updateServer(server.id, { is_active: !server.is_active })
    message.success(server.is_active ? t('mcp.serverDisabled') : t('mcp.serverEnabled'))
    await load()
  } catch (e: any) {
    message.error(e.message || t('mcp.operationFailed'))
  }
}

// ── Test ──

async function handleTest(server: MCPServer) {
  testingId.value = server.id
  testServerName.value = server.name
  testResult.value = null
  try {
    const result = await testServer(server.id)
    testResult.value = result
  } catch (e: any) {
    testResult.value = { ok: false, error: e.message || t('mcp.testRequestFailed') }
  } finally {
    testingId.value = ''
    showTestModal.value = true
  }
}

async function handleRefresh() {
  try {
    const result = await refreshTools()
    message.success(t('mcp.refreshComplete', { servers: result.servers, tools: result.total_tools }))
  } catch (e: any) {
    message.error(e.message || t('mcp.refreshFailed'))
  }
}
</script>

<template>
  <div class="page-container">
    <PageHeader :title="t('mcp.pageTitle')" :icon="Flash">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NButton size="small" @click="handleRefresh">
          <template #icon><NIcon><Refresh /></NIcon></template>
          {{ t('mcp.refreshTools') }}
        </NButton>
        <NButton size="small" type="primary" @click="openCreate">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('mcp.registerServer') }}
        </NButton>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" :placeholder="t('mcp.searchPlaceholder')" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterActive" :options="activeOptions" :placeholder="t('common.status')" size="small" style="width:130px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>{{ t('common.reset') }}</NButton>
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && servers.length === 0" :description="t('mcp.empty')" />
      <div class="mcp-list" v-if="servers.length > 0">
        <NCard
          v-for="server in servers"
          :key="server.id"
          size="small"
          :class="['mcp-card', { 'mcp-card-disabled': !server.is_active }]"
          hoverable
        >
          <div class="mcp-card-header">
            <div class="mcp-card-title-wrap">
              <span class="mcp-name" :title="server.name">{{ server.name }}</span>
              <NTag v-if="!server.is_active" size="tiny" :bordered="false" type="default" class="mcp-disabled-tag">{{ t('common.disabled') }}</NTag>
            </div>
            <div class="mcp-card-toggle" @click.stop>
              <StatusToggle
                :value="server.is_active"
                @update:value="() => handleToggle(server)"
              />
            </div>
          </div>

          <div class="mcp-card-row">
            <span class="mcp-card-label">{{ t('mcp.transport') }}</span>
            <NTag :type="server.transport_type === 'http' ? 'info' : 'warning'" size="tiny" :bordered="false">{{ server.transport_type }}</NTag>
          </div>

          <div class="mcp-card-row">
            <span class="mcp-card-label">{{ t('mcp.address') }}</span>
            <span class="mcp-meta" :title="(server.endpoint || server.command) ?? undefined">{{ server.endpoint || server.command || '—' }}</span>
          </div>

          <div class="mcp-card-row">
            <span class="mcp-card-label">{{ t('mcp.timeout') }}</span>
            <span class="mcp-meta">{{ server.timeout_seconds }}s</span>
          </div>

          <template #footer>
            <NSpace justify="end">
              <NButton size="small" @click="openEdit(server)">
                <template #icon><NIcon><Create /></NIcon></template>
                {{ t('common.edit') }}
              </NButton>
              <NButton size="small" :loading="testingId === server.id" @click="handleTest(server)">
                <template #icon><NIcon><Flash /></NIcon></template>
                {{ t('mcp.test') }}
              </NButton>
              <NPopconfirm @positive-click="handleDelete(server)">
                <template #trigger>
                  <NButton size="small" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                    <template #icon><NIcon><Trash /></NIcon></template>
                    {{ t('common.delete') }}
                  </NButton>
                </template>
                {{ t('mcp.confirmDelete') }}
              </NPopconfirm>
            </NSpace>
          </template>
        </NCard>
      </div>

      <AppPagination :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />
    </NSpin>

      <!-- Test Result Modal -->
      <AppModal
        v-model:show="showTestModal"
        :title="testModalTitle"
        size="detail"
      >
        <template v-if="testResult">
          <template v-if="testResult.ok">
            <p>{{ testResult.message }}</p>
            <ul v-if="testResult.tools?.length" class="mcp-test-tools">
              <li v-for="t in testResult.tools" :key="t.name">
                <NText strong>{{ t.name }}</NText>
                <NText depth="3" style="margin-left:8px">{{ t.description }}</NText>
              </li>
            </ul>
          </template>
          <template v-else>
            <NText type="error">{{ testResult.error }}</NText>
          </template>
        </template>
      </AppModal>

    <!-- Create/Edit Modal -->
    <AppModal v-model:show="showModal" :title="t('mcp.modalTitle')" size="detail">
      <NForm :model="form" label-placement="left" label-width="100">
        <NFormItem :label="t('common.name')" required>
          <NInput v-model:value="form.name" :placeholder="t('mcp.namePlaceholder')" maxlength="200" />
        </NFormItem>
        <NFormItem :label="t('mcp.transportType')" required>
          <NSelect v-model:value="form.transport_type" :options="[{ label: 'HTTP', value: 'http' }, { label: 'stdio', value: 'stdio' }]" />
        </NFormItem>
        <template v-if="form.transport_type === 'http'">
          <NFormItem :label="t('mcp.endpoint')" required>
            <NInput v-model:value="form.endpoint" placeholder="https://example.com/mcp" maxlength="500" />
          </NFormItem>
        </template>
        <template v-else>
          <NFormItem :label="t('mcp.command')" required>
            <NInput v-model:value="form.command" :placeholder="t('mcp.commandPlaceholder')" maxlength="500" />
          </NFormItem>
          <NFormItem :label="t('mcp.argsJson')">
            <NInput v-model:value="form.args_json" :placeholder="t('mcp.argsPlaceholder')" />
          </NFormItem>
          <NFormItem :label="t('mcp.envJson')">
            <NInput v-model:value="form.env_json" :placeholder="t('mcp.envPlaceholder')" />
          </NFormItem>
        </template>
        <NFormItem :label="t('mcp.timeoutSeconds')">
          <NInputNumber v-model:value="form.timeout_seconds" :min="1" :max="300" />
        </NFormItem>
        <NFormItem :label="t('common.enable')">
          <NSwitch v-model:value="form.is_active" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" :disabled="!form.name" @click="handleSave">{{ t('common.save') }}</NButton>
        </NSpace>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
/* MCP card grid (style reference: SkillsView.vue) */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.mcp-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.mcp-card {
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.mcp-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.mcp-card-disabled {
  background: var(--color-card-bg-disabled);
  --n-color: var(--color-card-bg-disabled);
  cursor: not-allowed;
}
.mcp-card-disabled:hover {
  border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transform: none;
}
.mcp-card-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}
.mcp-card-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mcp-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}
.mcp-card-toggle {
  flex-shrink: 0;
}
.mcp-card-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-xs);
  margin-bottom: 8px;
}
.mcp-card-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
  width: 36px;
}
.mcp-meta {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
