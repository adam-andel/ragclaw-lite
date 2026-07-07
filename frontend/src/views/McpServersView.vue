<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NDataTable, NButton, NModal, NForm, NFormItem, NInput, NSwitch,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NTag, NSelect, NInputNumber, NText,
} from 'naive-ui'
import { Add, Trash, Create, Flash, Refresh } from '@vicons/ionicons5'
import {
  listServers, createServer, updateServer, deleteServer, testServer, refreshTools,
} from '@/api/mcp'
import type { MCPServer, MCPServerCreatePayload } from '@/types'

const message = useMessage()

// ── Data ──

const servers = ref<MCPServer[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)

const showModal = ref(false)
const editing = ref<MCPServer | null>(null)
const form = ref<MCPServerCreatePayload & { id?: string }>({
  name: '', transport_type: 'http', endpoint: '', command: '', args_json: '', env_json: '',
  timeout_seconds: 30, is_active: true,
})

const testingId = ref('')
const testResult = ref<{ ok?: boolean; message?: string; error?: string; tools?: any[] } | null>(null)

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listServers(page.value, 20)
    servers.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
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
        endpoint: form.value.transport_type === 'http' ? form.value.endpoint : null,
        command: form.value.transport_type === 'stdio' ? form.value.command : null,
        args_json: form.value.args_json || null,
        env_json: form.value.env_json || null,
        timeout_seconds: form.value.timeout_seconds,
        is_active: form.value.is_active,
      })
      message.success('MCP 服务已更新')
    } else {
      await createServer({
        name: form.value.name, transport_type: form.value.transport_type,
        endpoint: form.value.transport_type === 'http' ? form.value.endpoint : null,
        command: form.value.transport_type === 'stdio' ? form.value.command : null,
        args_json: form.value.args_json || null,
        env_json: form.value.env_json || null,
        timeout_seconds: form.value.timeout_seconds,
        is_active: form.value.is_active,
      })
      message.success('MCP 服务已创建')
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  }
}

async function handleDelete(server: MCPServer) {
  try {
    await deleteServer(server.id)
    message.success('MCP 服务已删除')
    await load()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

// ── Test ──

async function handleTest(server: MCPServer) {
  testingId.value = server.id
  testResult.value = null
  try {
    const result = await testServer(server.id)
    testResult.value = result
  } catch (e: any) {
    testResult.value = { ok: false, error: e.message || '测试请求失败' }
  } finally {
    testingId.value = ''
  }
}

async function handleRefresh() {
  try {
    const result = await refreshTools()
    message.success(`工具刷新完成：${result.servers} 个服务，${result.total_tools} 个工具`)
  } catch (e: any) {
    message.error(e.message || '刷新失败')
  }
}

// ── Columns ──

const columns = [
  { title: '名称', key: 'name', width: 160, render: (row: MCPServer) => h('span', [h(NText, { strong: true }, { default: () => row.name }), row.is_active ? null : h(NTag, { size: 'tiny', type: 'default', style: 'margin-left:8px' }, { default: () => '禁用' })]) },
  { title: '传输', key: 'transport_type', width: 80, render: (row: MCPServer) => h(NTag, { type: row.transport_type === 'http' ? 'info' : 'warning', size: 'small' }, { default: () => row.transport_type }) },
  { title: '地址', key: 'endpoint', width: 260, ellipsis: { tooltip: true }, render: (row: MCPServer) => row.endpoint || row.command || '-' },
  { title: '超时(s)', key: 'timeout_seconds', width: 80 },
  {
    title: '操作', key: 'actions', width: 240,
    render: (row: MCPServer) =>
      h(NSpace, null, {
        default: () => [
          h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { icon: () => h(NIcon, null, { default: () => h(Create) }), default: () => '编辑' }),
          h(NButton, { size: 'tiny', quaternary: true, loading: testingId.value === row.id, onClick: () => handleTest(row) }, { icon: () => h(NIcon, null, { default: () => h(Flash) }), default: () => '测试' }),
          h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { icon: () => h(NIcon, null, { default: () => h(Trash) }), default: () => '删除' }),
            default: () => '确认删除此 MCP 服务？',
          }),
        ],
      }),
  },
]
</script>

<template>
  <div class="page-container">
    <div class="dm-header">
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><Flash /></NIcon>
        <h2>MCP 服务管理</h2>
        <span v-if="total > 0" class="kb-header-badge">{{ total }}</span>
      </div>
      <div class="dm-header-actions">
        <NButton size="small" @click="handleRefresh">
          <NIcon><Refresh /></NIcon> 刷新工具
        </NButton>
        <NButton type="primary" size="small" @click="openCreate">
          <NIcon><Add /></NIcon> 注册服务
        </NButton>
      </div>
    </div>

      <NDataTable
        :columns="columns"
        :data="servers"
        :loading="loading"
        :pagination="{ page, pageSize: 20, itemCount: total, showSizePicker: false, onChange: (p: number) => { page = p; load() } }"
        :row-key="(r: MCPServer) => r.id"
      />

      <!-- Test Result -->
      <div v-if="testResult" style="margin-top:12px">
        <NCard size="small" :title="testResult.ok ? '✅ 连接成功' : '❌ 连接失败'">
          <template v-if="testResult.ok">
            <p>{{ testResult.message }}</p>
            <ul v-if="testResult.tools?.length">
              <li v-for="t in testResult.tools" :key="t.name">
                <NText strong>{{ t.name }}</NText>
                <NText depth="3" style="margin-left:8px">{{ t.description }}</NText>
              </li>
            </ul>
          </template>
          <template v-else>
            <NText type="error">{{ testResult.error }}</NText>
          </template>
        </NCard>
      </div>

    <!-- Create/Edit Modal -->
    <NModal v-model:show="showModal" title="MCP 服务" preset="card" style="width:640px">
      <NForm :model="form" label-placement="left" label-width="100">
        <NFormItem label="名称" required>
          <NInput v-model:value="form.name" placeholder="如：天气查询" maxlength="200" />
        </NFormItem>
        <NFormItem label="传输方式" required>
          <NSelect v-model:value="form.transport_type" :options="[{ label: 'HTTP', value: 'http' }, { label: 'stdio', value: 'stdio' }]" />
        </NFormItem>
        <template v-if="form.transport_type === 'http'">
          <NFormItem label="Endpoint" required>
            <NInput v-model:value="form.endpoint" placeholder="https://example.com/mcp" maxlength="500" />
          </NFormItem>
        </template>
        <template v-else>
          <NFormItem label="命令" required>
            <NInput v-model:value="form.command" placeholder="如：npx -y @modelcontextprotocol/server-weather" maxlength="500" />
          </NFormItem>
          <NFormItem label="参数 (JSON)">
            <NInput v-model:value="form.args_json" placeholder='如：["--port", "9999"]' />
          </NFormItem>
          <NFormItem label="环境变量 (JSON)">
            <NInput v-model:value="form.env_json" placeholder='如：{"API_KEY": "xxx"}' />
          </NFormItem>
        </template>
        <NFormItem label="超时(秒)">
          <NInputNumber v-model:value="form.timeout_seconds" :min="1" :max="300" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="form.is_active" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" :disabled="!form.name" @click="handleSave">保存</NButton>
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
</style>
