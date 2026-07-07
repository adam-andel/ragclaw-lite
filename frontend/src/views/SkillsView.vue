<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NDataTable, NButton, NButtonGroup, NModal, NForm, NFormItem, NInput, NSwitch,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NPopover, NTag, NText, NSelect,
  NUpload, NDivider, NScrollbar,
} from 'naive-ui'
import { Add, Trash, Create, CloudUpload, Sync, ChevronDown, Bulb } from '@vicons/ionicons5'
import {
  listSkills, createSkill, updateSkill, deleteSkill, getSkill,
  uploadFolder, uploadZip, syncSkills, toggleSkill, reuploadFolder, reuploadZip,
} from '@/api/skills'
import { listServers } from '@/api/mcp'
import type { Skill, SkillCreatePayload, MCPServer } from '@/types'

const message = useMessage()

// ── Data ──
const skills = ref<Skill[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)

// Create modal
const showCreateModal = ref(false)
const createForm = ref<SkillCreatePayload>({
  name: '', description: '', mcp_servers: [], is_active: true, body: '',
})

// Edit modal (SKILL.md editor)
const showEditModal = ref(false)
const editingSkill = ref<Skill | null>(null)
const skillMdContent = ref('')

// Re-upload state
const reuploadFolderInput = ref<HTMLInputElement>()
const reuploadSkillId = ref<string>('')

// MCP servers for options
const servers = ref<MCPServer[]>([])
const serverOptions = ref<{ label: string; value: string }[]>([])

// Folder upload input ref (global upload)
const folderInput = ref<HTMLInputElement>()

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
      label: s.name,
      value: s.name,
    }))
  } catch { /* ignore */ }
}

// ── Create ──

function openCreate() {
  createForm.value = { name: '', description: '', mcp_servers: [], is_active: true, body: '' }
  loadServers()
  showCreateModal.value = true
}

async function handleCreate() {
  try {
    await createSkill(createForm.value)
    message.success('技能已创建')
    showCreateModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || '创建失败')
  }
}

// ── Edit SKILL.md ──

async function openEdit(skill: Skill) {
  try {
    const full = await getSkill(skill.id)
    editingSkill.value = full
    skillMdContent.value = full.skill_md_content || ''
    showEditModal.value = true
  } catch (e: any) {
    message.error(e.message || '加载技能详情失败')
  }
}

async function handleSaveEdit() {
  if (!editingSkill.value) return
  try {
    await updateSkill(editingSkill.value.id, { content: skillMdContent.value })
    message.success('SKILL.md 已保存')
    showEditModal.value = false
    await load()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  }
}

// ── Delete ──

async function handleDelete(skill: Skill) {
  try {
    await deleteSkill(skill.id)
    message.success('技能已删除')
    await load()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

// ── Folder Upload ──

function triggerFolderUpload() {
  folderInput.value?.click()
}

function handleFolderChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return

  const files = Array.from(input.files)
  // Extract relative paths from webkitRelativePath
  const paths = files.map(f => (f as any).webkitRelativePath || f.name)

  // Validate: must contain SKILL.md
  const hasSkillMd = paths.some(p => p.toUpperCase().endsWith('SKILL.MD'))
  if (!hasSkillMd) {
    message.error('上传的文件夹必须包含 SKILL.md')
    input.value = ''
    return
  }

  doFolderUpload(files, paths)
  input.value = ''
}

async function doFolderUpload(files: File[], paths: string[]) {
  loading.value = true
  try {
    await uploadFolder(files, paths)
    message.success('技能文件夹上传成功')
    await load()
  } catch (e: any) {
    message.error(e.message || '上传失败')
  } finally {
    loading.value = false
  }
}

// ── ZIP Upload ──

async function handleZipUpload(options: any) {
  const file = options.file.file
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error('请上传 .zip 文件')
    return
  }
  loading.value = true
  try {
    await uploadZip(file)
    message.success('ZIP 上传成功')
    await load()
  } catch (e: any) {
    message.error(e.message || '上传失败')
  } finally {
    loading.value = false
  }
}

// ── Re-upload ──

function triggerReuploadFolder(skillId: string) {
  reuploadSkillId.value = skillId
  reuploadFolderInput.value?.click()
}

function handleReuploadFolderChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return

  const files = Array.from(input.files)
  const paths = files.map(f => (f as any).webkitRelativePath || f.name)

  const hasSkillMd = paths.some(p => p.toUpperCase().endsWith('SKILL.MD'))
  if (!hasSkillMd) {
    message.error('上传的文件夹必须包含 SKILL.md')
    input.value = ''
    return
  }

  doReuploadFolder(files, paths)
  input.value = ''
}

async function doReuploadFolder(files: File[], paths: string[]) {
  if (!reuploadSkillId.value) return
  loading.value = true
  try {
    await reuploadFolder(reuploadSkillId.value, files, paths)
    message.success('技能文件夹已重新上传并替换')
    await load()
  } catch (e: any) {
    message.error(e.message || '重新上传失败')
  } finally {
    loading.value = false
  }
}

async function handleReuploadZip(skillId: string, options: any) {
  const file = options.file.file
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error('请上传 .zip 文件')
    return
  }
  loading.value = true
  try {
    await reuploadZip(skillId, file)
    message.success('技能 ZIP 已重新上传并替换')
    await load()
  } catch (e: any) {
    message.error(e.message || '重新上传失败')
  } finally {
    loading.value = false
  }
}

// ── Enable / Disable ──

async function handleToggle(skill: Skill) {
  try {
    await toggleSkill(skill.id)
    message.success(skill.is_active ? '技能已禁用' : '技能已启用')
    await load()
  } catch (e: any) {
    message.error(e.message || '操作失败')
  }
}

// ── Sync ──

async function handleSync() {
  loading.value = true
  try {
    const result = await syncSkills()
    message.success(`同步完成：新增 ${result.added}，更新 ${result.updated}，停用 ${result.deactivated}`)
    await load()
  } catch (e: any) {
    message.error(e.message || '同步失败')
  } finally {
    loading.value = false
  }
}

// ── Columns ──

const columns = [
  { title: '名称', key: 'name', width: 140,
    render: (row: Skill) => h('span', [
      h(NText, { strong: true }, { default: () => row.name }),
      row.is_active ? null : h(NTag, { size: 'tiny', type: 'default', style: 'margin-left:8px' }, { default: () => '禁用' }),
    ]),
  },
  { title: '文件夹', key: 'folder_name', width: 140 },
  { title: 'MCP服务', key: 'mcp_servers', width: 120,
    render: (row: Skill) => row.mcp_servers?.length
      ? h(NSpace, { size: 'small' }, { default: () => row.mcp_servers.map((s: string) => h(NTag, { size: 'tiny', type: 'info' }, { default: () => s })) })
      : h(NText, { depth: 3 }, { default: () => '无' }),
  },
  { title: '更新时间', key: 'updated_at', width: 150, render: (row: Skill) => row.updated_at?.slice(0, 16)?.replace('T', ' ') || '-' },
  {
    title: '操作', key: 'actions', width: 320,
    render: (row: Skill) => h(NSpace, { size: 'small', align: 'center' }, {
      default: () => [
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => openEdit(row) },
          { icon: () => h(NIcon, null, { default: () => h(Create) }), default: () => '编辑' }),
        h(NButtonGroup, null, {
          default: () => [
            h(NButton, {
              size: 'tiny',
              quaternary: true,
              onClick: () => triggerReuploadFolder(row.id)
            }, {
              icon: () => h(NIcon, null, { default: () => h(CloudUpload) }),
              default: () => '重新上传'
            }),
            h(NPopover, { trigger: 'click', placement: 'bottom-start', showArrow: false }, {
              trigger: () => h(NButton, {
                size: 'tiny',
                quaternary: true,
                style: 'padding: 0 4px; min-width: 20px;'
              }, {
                default: () => h(NIcon, { size: 12 }, { default: () => h(ChevronDown) })
              }),
              default: () => h(NUpload, {
                'show-file-list': false,
                customRequest: (o: any) => handleReuploadZip(row.id, o)
              }, {
                default: () => h(NButton, { size: 'tiny' }, {
                  icon: () => h(NIcon, null, { default: () => h(CloudUpload) }),
                  default: () => '重新上传ZIP'
                })
              })
            }),
          ]
        }),
        h(NSwitch, {
          size: 'small',
          value: row.is_active,
          'on-update:value': () => handleToggle(row),
        }, {
          checked: () => '启用',
          unchecked: () => '禁用',
        }),
        h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
          trigger: () => h(NButton, { size: 'tiny', quaternary: true, type: 'error' },
            { icon: () => h(NIcon, null, { default: () => h(Trash) }), default: () => '删除' }),
          default: () => '确认删除此技能？文件夹和DB记录都会被删除。',
        }),
      ],
    }),
  },
]

// ── Init ──

onMounted(() => {
  load()
  // Set webkitdirectory attribute on folder inputs
  if (folderInput.value) {
    folderInput.value.setAttribute('webkitdirectory', '')
  }
  if (reuploadFolderInput.value) {
    reuploadFolderInput.value.setAttribute('webkitdirectory', '')
  }
})
</script>

<template>
  <div class="page-container">
    <div class="dm-header">
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><Bulb /></NIcon>
        <h2>技能管理</h2>
        <span v-if="total > 0" class="kb-header-badge">{{ total }}</span>
      </div>
      <div class="dm-header-actions">
        <NButton size="small" @click="handleSync">
          <template #icon><NIcon><Sync /></NIcon></template>
          同步
        </NButton>
        <NButton size="small" @click="triggerFolderUpload">
          <template #icon><NIcon><CloudUpload /></NIcon></template>
          上传文件夹
        </NButton>
        <NUpload :show-file-list="false" :custom-request="handleZipUpload" accept=".zip">
          <NButton size="small">
            <template #icon><NIcon><CloudUpload /></NIcon></template>
            上传ZIP
          </NButton>
        </NUpload>
        <NButton type="primary" size="small" @click="openCreate">
          <template #icon><NIcon><Add /></NIcon></template>
          在线创建
        </NButton>
      </div>
    </div>

    <!-- Hidden folder inputs -->
    <input ref="folderInput" type="file" style="display:none" @change="handleFolderChange" />
    <input ref="reuploadFolderInput" type="file" style="display:none" @change="handleReuploadFolderChange" />

    <NDataTable
      :columns="columns"
      :data="skills"
      :loading="loading"
      :pagination="{ page, pageSize: 20, itemCount: total, showSizePicker: false, onChange: (p: number) => { page = p; load() } }"
      :row-key="(r: Skill) => r.id"
    />

    <!-- Create Modal -->
    <NModal v-model:show="showCreateModal" title="在线创建技能" preset="card" style="width:640px">
      <NForm :model="createForm" label-placement="left" label-width="100">
        <NFormItem label="名称" required>
          <NInput v-model:value="createForm.name" placeholder="如：IT运维助手" maxlength="200" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="createForm.description" type="textarea"
            placeholder="≤250字符，给LLM路由看的技能描述" rows="2" maxlength="250" />
        </NFormItem>
        <NFormItem label="MCP服务">
          <NSelect v-model:value="createForm.mcp_servers" :options="serverOptions" multiple
            placeholder="选择该技能可用的MCP服务（可选）" />
        </NFormItem>
        <NFormItem label="SKILL正文">
          <NInput v-model:value="createForm.body" type="textarea"
            placeholder="SKILL.md 的 Markdown 正文（front matter 自动生成）" rows="8" />
        </NFormItem>
        <NFormItem label="启用">
          <NSwitch v-model:value="createForm.is_active" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreateModal = false">取消</NButton>
          <NButton type="primary" :disabled="!createForm.name" @click="handleCreate">创建</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Edit SKILL.md Modal -->
    <NModal v-model:show="showEditModal" title="编辑 SKILL.md" preset="card" style="width:80vw;max-width:800px">
      <div style="margin-bottom:8px">
        <NText depth="3" style="font-size:12px">
          直接编辑 SKILL.md 全文。YAML front matter 中的 name/description/mcp_servers 会同步到数据库索引，is_active 通过上方开关管理。
        </NText>
      </div>
      <NInput v-model:value="skillMdContent" type="textarea"
        :autosize="{ minRows: 20, maxRows: 30 }"
        style="font-family: monospace; font-size: 13px"
        placeholder="---\nname: ...\ndescription: ...\nmcp_servers:\n  - ...\n---\n\n# 正文" />
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditModal = false">取消</NButton>
          <NButton type="primary" @click="handleSaveEdit">保存</NButton>
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
