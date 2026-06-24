<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NDataTable, NButton, NModal, NForm, NFormItem, NInput, NSwitch,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NTag, NText, NSelect,
} from 'naive-ui'
import { Add, Trash, Create, Build } from '@vicons/ionicons5'
import {
  listSkills, createSkill, updateSkill, deleteSkill, getSkill,
  bindTool, unbindTool,
} from '@/api/skills'
import { listServers } from '@/api/mcp'
import type { Skill, SkillCreatePayload, MCPServer } from '@/types'

const message = useMessage()

// ── Data ──

const skills = ref<Skill[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)

const showModal = ref(false)
const editing = ref<Skill | null>(null)
const form = ref<SkillCreatePayload & { id?: string }>({
  name: '', description: '', system_prompt: '', is_active: true,
})

// Tool binding modal
const showToolModal = ref(false)
const toolSkillId = ref('')
const toolForm = ref({ tool_name: '', mcp_server_id: '' })
const servers = ref<MCPServer[]>([])
const serverOptions = ref<{ label: string; value: string }[]>([])

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listSkills(page.value, 20)
    skills.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadServers() {
  try {
    const data = await listServers(1, 100)
    servers.value = data.items
    serverOptions.value = data.items.filter(s => s.is_active).map(s => ({
      label: `${s.name} (${s.transport_type})`,
      value: s.id,
    }))
  } catch { /* ignore */ }
}

// ── CRUD ──

function openCreate() {
  editing.value = null
  form.value = { name: '', description: '', system_prompt: '', is_active: true }
  showModal.value = true
}

async function openEdit(skill: Skill) {
  try {
    const full = await getSkill(skill.id)
    editing.value = full
    form.value = {
      id: full.id,
      name: full.name,
      description: full.description || '',
      system_prompt: full.system_prompt,
      is_active: full.is_active,
    }
    showModal.value = true
  } catch (e: any) {
    message.error(e.message || '加载技能详情失败')
  }
}

async function handleSave() {
  try {
    if (editing.value) {
      await updateSkill(editing.value.id, {
        name: form.value.name,
        description: form.value.description,
        system_prompt: form.value.system_prompt,
        is_active: form.value.is_active,
      })
      message.success('技能已更新')
    } else {
      await createSkill({ name: form.value.name, description: form.value.description, system_prompt: form.value.system_prompt || '', is_active: form.value.is_active })
      message.success('技能已创建')
    }
    showModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  }
}

async function handleDelete(skill: Skill) {
  try {
    await deleteSkill(skill.id)
    message.success('技能已删除')
    await load()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

// ── Tools ──

function openToolBind(skill: Skill) {
  toolSkillId.value = skill.id
  toolForm.value = { tool_name: '', mcp_server_id: '' }
  loadServers()
  showToolModal.value = true
}

async function handleBindTool() {
  try {
    await bindTool(toolSkillId.value, toolForm.value)
    message.success('工具已绑定')
    showToolModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || '绑定失败')
  }
}

async function handleUnbindTool(skillId: string, toolId: string) {
  try {
    await unbindTool(skillId, toolId)
    message.success('工具已解绑')
    await load()
  } catch (e: any) {
    message.error(e.message || '解绑失败')
  }
}

// ── Columns ──

const columns = [
  { title: '名称', key: 'name', width: 160,
    render: (row: Skill) => h('span', [h(NText, { strong: true }, { default: () => row.name }), row.is_active ? null : h(NTag, { size: 'tiny', type: 'default', style: 'margin-left:8px' }, { default: () => '禁用' })]) },
  { title: '描述', key: 'description', ellipsis: { tooltip: true }, width: 240 },
  { title: '工具数', key: 'tools', width: 80, render: (row: Skill) => row.tools?.length || 0 },
  { title: '更新时间', key: 'updated_at', width: 160, render: (row: Skill) => row.updated_at?.slice(0, 16)?.replace('T', ' ') || '-' },
  {
    title: '操作', key: 'actions', width: 200,
    render: (row: Skill) =>
      h(NSpace, null, {
        default: () => [
          h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) }, { icon: () => h(NIcon, null, { default: () => h(Create) }), default: () => '编辑' }),
          h(NButton, { size: 'tiny', quaternary: true, onClick: () => openToolBind(row) }, { icon: () => h(NIcon, null, { default: () => h(Build) }), default: () => '绑定工具' }),
          h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
            trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' }, { icon: () => h(NIcon, null, { default: () => h(Trash) }), default: () => '删除' }),
            default: () => '确认删除此技能？',
          }),
        ],
      }),
  },
]

// ── Init ──

import { h } from 'vue'
onMounted(load)
</script>

<template>
  <div class="page-container">
    <NCard title="技能管理" size="small">
      <template #header-extra>
        <NButton type="primary" size="small" @click="openCreate">
          <NIcon><Add /></NIcon> 创建技能
        </NButton>
      </template>

      <NDataTable
        :columns="columns"
        :data="skills"
        :loading="loading"
        :pagination="{ page, pageSize: 20, itemCount: total, showSizePicker: false, onChange: (p: number) => { page = p; load() } }"
        :row-key="(r: Skill) => r.id"
      />
    </NCard>

    <!-- Create/Edit Modal -->
    <NModal v-model:show="showModal" title="技能信息" preset="card" style="width:640px">
      <NForm :model="form" label-placement="left" label-width="100">
        <NFormItem label="名称" required>
          <NInput v-model:value="form.name" placeholder="如：IT运维助手" maxlength="200" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" type="textarea" placeholder="给LLM路由看的技能描述（可选）" rows="2" maxlength="500" />
        </NFormItem>
        <NFormItem label="System Prompt">
          <NInput v-model:value="form.system_prompt" type="textarea" placeholder="该技能的专属系统提示词" rows="6" maxlength="10000" />
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

    <!-- Tool Bind Modal -->
    <NModal v-model:show="showToolModal" title="绑定 MCP 工具" preset="card" style="width:480px">
      <NForm :model="toolForm" label-placement="left" label-width="100">
        <NFormItem label="MCP 服务" required>
          <NSelect v-model:value="toolForm.mcp_server_id" :options="serverOptions" placeholder="选择 MCP 服务" />
        </NFormItem>
        <NFormItem label="工具名称" required>
          <NInput v-model:value="toolForm.tool_name" placeholder="如：get_weather" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showToolModal = false">取消</NButton>
          <NButton type="primary" :disabled="!toolForm.tool_name || !toolForm.mcp_server_id" @click="handleBindTool">绑定</NButton>
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
</style>
