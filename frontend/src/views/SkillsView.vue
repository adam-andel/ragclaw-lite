<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NButton, NForm, NFormItem, NInput, NSwitch,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NPopover, NTag, NText, NSelect,
  NUpload, NEmpty, NSpin, NDescriptions, NDescriptionsItem,
} from 'naive-ui'
import { Add, Trash, Create, CloudUpload, Sync, Bulb, Ban, CheckmarkCircle, Search } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  listSkills, createSkill, updateSkill, deleteSkill, getSkill,
  uploadFolder, uploadZip, syncSkills, toggleSkill, reuploadFolder, reuploadZip,
} from '@/api/skills'
import StatusToggle from '@/components/common/StatusToggle.vue'
import { listServers } from '@/api/mcp'
import type { Skill, SkillCreatePayload, MCPServer } from '@/types'

const message = useMessage()

// ── Data ──
const skills = ref<Skill[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20

// Filters — search + enable/disable (mirrors DocumentManage.vue)
const search = ref('')
const filterActive = ref<'all' | 'active' | 'inactive'>('all')
const activeOptions = [
  { label: '全部', value: 'all' },
  { label: '已启用', value: 'active' },
  { label: '已禁用', value: 'inactive' },
]

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

// Detail modal
const showDetail = ref(false)
const detailSkill = ref<Skill | null>(null)
const detailLoading = ref(false)

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listSkills(
      page.value, pageSize,
      search.value || undefined,
      filterActive.value === 'all' ? undefined : filterActive.value === 'active',
    )
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

// ── Detail ──

async function openDetail(skill: Skill) {
  detailLoading.value = true
  showDetail.value = true
  try {
    detailSkill.value = await getSkill(skill.id)
  } catch (e: any) {
    message.error(e.message || '加载技能详情失败')
    detailSkill.value = skill
  } finally {
    detailLoading.value = false
  }
}

// ── Delete ──

async function handleDelete(skill: Skill) {
  try {
    await deleteSkill(skill.id)
    message.success('技能已删除')
    if (detailSkill.value?.id === skill.id) showDetail.value = false
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
    if (detailSkill.value?.id === skill.id) {
      detailSkill.value = { ...detailSkill.value, is_active: !detailSkill.value.is_active }
    }
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

function formatTime(t?: string | null) {
  if (!t) return '-'
  return t.slice(0, 16)?.replace('T', ' ') || '-'
}

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
    <PageHeader title="技能管理" :icon="Bulb">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
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
        <NButton size="small" type="primary" @click="openCreate">
          <template #icon><NIcon><Add /></NIcon></template>
          在线创建
        </NButton>
      </template>
    </PageHeader>

    <!-- Hidden folder inputs -->
    <input ref="folderInput" type="file" style="display:none" @change="handleFolderChange" />
    <input ref="reuploadFolderInput" type="file" style="display:none" @change="handleReuploadFolderChange" />

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" placeholder="搜索技能名称或描述…" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        搜索
      </NButton>
      <NSelect v-model:value="filterActive" :options="activeOptions" placeholder="状态" size="small" style="width:130px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>重置</NButton>
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && skills.length === 0" description="暂无技能，请上传或在线创建" />
      <div class="sk-list" v-if="skills.length > 0">
        <NCard
          v-for="skill in skills"
          :key="skill.id"
          size="small"
          :class="['sk-card', { 'sk-card-disabled': !skill.is_active }]"
          hoverable
          role="button"
          tabindex="0"
          @click="openDetail(skill)"
          @keydown.enter.prevent="openDetail(skill)"
          @keydown.space.prevent="openDetail(skill)"
        >
          <div class="sk-card-header">
            <div class="sk-card-title-wrap">
              <span class="sk-name" :title="skill.name">{{ skill.name }}</span>
              <NTag v-if="!skill.is_active" size="tiny" :bordered="false" type="default" class="sk-disabled-tag">禁用</NTag>
            </div>
            <div class="sk-card-toggle" @click.stop>
              <StatusToggle
                :value="skill.is_active"
                @update:value="() => handleToggle(skill)"
              />
            </div>
          </div>

          <p class="sk-card-desc" :title="skill.description ?? undefined">{{ skill.description || '暂无描述' }}</p>

          <div class="sk-card-mcp">
            <span class="sk-card-label">MCP服务</span>
            <template v-if="skill.mcp_servers && skill.mcp_servers.length">
              <NTag
                v-for="s in skill.mcp_servers"
                :key="s"
                size="tiny"
                type="info"
                :bordered="false"
                class="sk-mcp-tag"
              >{{ s }}</NTag>
            </template>
            <span v-else class="sk-meta-muted">无</span>
          </div>

          <div class="sk-card-meta">
            <span class="sk-meta-muted">📁 {{ skill.folder_name }}</span>
            <span class="sk-meta-sep">·</span>
            <span class="sk-meta-muted">更新 {{ formatTime(skill.updated_at) }}</span>
          </div>
        </NCard>
      </div>
    </NSpin>

    <AppPagination :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />

    <!-- Create Modal -->
    <AppModal v-model:show="showCreateModal" title="在线创建技能" size="detail">
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
    </AppModal>

    <!-- Edit SKILL.md Modal -->
    <AppModal v-model:show="showEditModal" title="编辑 2SKILL.md" size="code">
      <div style="margin-bottom:8px">
        <NText depth="3" style="font-size:12px">
          直接编辑 SKILL.md 全文。YAML front matter 中的 name/description/mcp_servers 会同步到数据库索引，is_active 通过上方开关管理。
        </NText>
      </div>
      <NInput v-model:value="skillMdContent" type="textarea"
        :autosize="{ minRows: 14, maxRows: 18 }"
        style="font-family: monospace; font-size: 13px"
        placeholder="---\nname: ...\ndescription: ...\nmcp_servers:\n  - ...\n---\n\n# 正文" />
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditModal = false">取消</NButton>
          <NButton type="primary" @click="handleSaveEdit">保存</NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Detail Modal -->
    <AppModal
      v-model:show="showDetail"
      :title="detailSkill?.name || '技能详情'"
      size="detail"
    >
      <NSpin :show="detailLoading">
        <NDescriptions
          v-if="detailSkill"
          bordered
          :column="1"
          size="small"
          label-placement="left"
          label-style="width: 110px"
        >
          <NDescriptionsItem label="名称">
            <span class="sk-detail-name">{{ detailSkill.name }}</span>
            <NTag v-if="!detailSkill.is_active" size="tiny" :bordered="false" type="default" style="margin-left:8px">禁用</NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="描述">
            <span v-if="detailSkill.description">{{ detailSkill.description }}</span>
            <span v-else class="sk-meta-muted">暂无描述</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="文件夹">{{ detailSkill.folder_name }}</NDescriptionsItem>
          <NDescriptionsItem label="MCP服务">
            <template v-if="detailSkill.mcp_servers && detailSkill.mcp_servers.length">
              <NSpace :size="6">
                <NTag v-for="s in detailSkill.mcp_servers" :key="s" size="tiny" type="info" :bordered="false">{{ s }}</NTag>
              </NSpace>
            </template>
            <span v-else class="sk-meta-muted">无</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="状态">
            <NTag :type="detailSkill.is_active ? 'success' : 'default'" size="small">
              {{ detailSkill.is_active ? '已启用' : '已禁用' }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="创建时间">{{ formatTime(detailSkill.created_at) }}</NDescriptionsItem>
          <NDescriptionsItem label="更新时间">{{ formatTime(detailSkill.updated_at) }}</NDescriptionsItem>
          <NDescriptionsItem label="技能 ID">
            <span class="sk-id">{{ detailSkill.id }}</span>
          </NDescriptionsItem>
        </NDescriptions>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailSkill" @click="openEdit(detailSkill)">
            <template #icon><NIcon size="16"><Create /></NIcon></template>
            编辑
          </NButton>
          <NButton size="small" v-if="detailSkill" @click="triggerReuploadFolder(detailSkill.id)">
            <template #icon><NIcon size="16"><CloudUpload /></NIcon></template>
            重新上传
          </NButton>
          <NPopover v-if="detailSkill" trigger="click" placement="top-end" show-arrow>
            <template #trigger>
              <NButton size="small">
                <template #icon><NIcon size="16"><CloudUpload /></NIcon></template>
                重新上传ZIP
              </NButton>
            </template>
            <NUpload
              :show-file-list="false"
              :custom-request="(o: any) => handleReuploadZip(detailSkill!.id, o)"
            >
              <NButton size="small">选择 ZIP 文件</NButton>
            </NUpload>
          </NPopover>
          <NButton size="small" v-if="detailSkill" @click="handleToggle(detailSkill)" :style="detailSkill.is_active
            ? { '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b', '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706', '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706' }
            : { '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e', '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a', '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a' }">
            <template #icon>
              <NIcon size="16">
                <Ban v-if="detailSkill.is_active" />
                <CheckmarkCircle v-else />
              </NIcon>
            </template>
            {{ detailSkill.is_active ? '禁用' : '启用' }}
          </NButton>
          <NPopconfirm v-if="detailSkill" @positive-click="handleDelete(detailSkill)">
            <template #trigger>
              <NButton size="small" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                <template #icon><NIcon size="16"><Trash /></NIcon></template>
                删除
              </NButton>
            </template>
            确认删除此技能？文件夹和DB记录都会被删除。
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>

  </div>
</template>

<style scoped>
/* Skill card grid */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.sk-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.sk-card {
  cursor: pointer;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.sk-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.sk-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.sk-card-disabled {
  background: var(--color-card-bg-disabled);
  --n-color: var(--color-card-bg-disabled);
  cursor: not-allowed;
}
.sk-card-disabled:hover {
  border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transform: none;
}

.sk-card-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
}
.sk-card-icon {
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-primary-soft);
  border-radius: var(--radius);
}
.sk-card-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sk-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 140px;
}
.sk-card-toggle {
  flex-shrink: 0;
}

.sk-card-desc {
  margin: 0 0 10px;
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.4em;
}

.sk-card-mcp {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.sk-card-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
}
.sk-mcp-tag {
  margin-right: 2px;
}

.sk-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-wrap: wrap;
}
.sk-meta-sep {
  color: var(--color-border);
  margin: 0 2px;
}
.sk-meta-muted {
  color: var(--color-text);
}

/* Detail modal */
.sk-detail-name {
  font-weight: 600;
}
.sk-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  word-break: break-all;
}
</style>
