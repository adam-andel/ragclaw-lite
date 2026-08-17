<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { backendErrorMessage } from '@/utils/backendError'
import { useI18n } from 'vue-i18n'
import {
  NButton, NForm, NFormItem, NInput, NSwitch,
  NIcon, useMessage, NSpace, NPopconfirm, NTag, NText, NSelect,
  NUpload, NEmpty, NSpin, NDescriptions, NDescriptionsItem,
} from 'naive-ui'
import { Add, Trash, Create, CloudUpload, Sync, Bulb, Ban, CheckmarkCircle, Search, FolderOpen, Archive } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  listSkills, createSkill, updateSkill, deleteSkill, getSkill,
  uploadFolder, uploadZip, syncSkills, toggleSkill, reuploadFolder, reuploadZip,
} from '@/api/skills'
import StatusToggle from '@/components/common/StatusToggle.vue'
import AppCard from '@/components/common/AppCard.vue'
import { listServers } from '@/api/mcp'
import type { Skill, SkillCreatePayload, MCPServer } from '@/types'

const message = useMessage()
const { t } = useI18n()

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
  { label: t('skills.statusAll'), value: 'all' },
  { label: t('common.enabled'), value: 'active' },
  { label: t('common.disabled'), value: 'inactive' },
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

// ZIP upload input ref (global upload)
const zipInput = ref<HTMLInputElement>()

// Detail modal
const showDetail = ref(false)
const detailSkill = ref<Skill | null>(null)
const detailLoading = ref(false)

// ── Secret-zero API KEY (per-skill injection proxy) ──
const apiKeyInput = ref('')
const apiKeySaving = ref(false)
// Status badge: derived from the skill's server-computed flag. The key itself is
// never sent back to the client — only whether one is configured.
const apiKeyConfigured = computed(() => !!detailSkill.value?.api_key_configured)

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
    message.error(backendErrorMessage(e.message) || t('skills.loadFailed'))
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
    message.success(t('skills.created'))
    showCreateModal.value = false
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.createFailed'))
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
    message.error(backendErrorMessage(e.message) || t('skills.loadDetailFailed'))
  }
}

async function handleSaveEdit() {
  if (!editingSkill.value) return
  try {
    await updateSkill(editingSkill.value.id, { content: skillMdContent.value })
    message.success(t('skills.skillMdSaved'))
    showEditModal.value = false
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.saveFailed'))
  }
}

// ── Detail ──

async function openDetail(skill: Skill) {
  detailLoading.value = true
  showDetail.value = true
  try {
    detailSkill.value = await getSkill(skill.id)
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.loadDetailFailed'))
    detailSkill.value = skill
  } finally {
    detailLoading.value = false
  }
}

// ── Secret-zero API KEY handlers ──
async function saveApiKey() {
  if (!detailSkill.value) return
  apiKeySaving.value = true
  try {
    const updated = await updateSkill(detailSkill.value.id, { api_key: apiKeyInput.value })
    detailSkill.value = updated
    apiKeyInput.value = ''
    message.success(t('skills.apiKeySaved'))
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.saveFailed'))
  } finally {
    apiKeySaving.value = false
  }
}

async function clearApiKey() {
  if (!detailSkill.value) return
  apiKeySaving.value = true
  try {
    const updated = await updateSkill(detailSkill.value.id, { api_key: '' })
    detailSkill.value = updated
    apiKeyInput.value = ''
    message.success(t('skills.apiKeyCleared'))
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.saveFailed'))
  } finally {
    apiKeySaving.value = false
  }
}

// ── Delete ──

async function handleDelete(skill: Skill) {
  try {
    await deleteSkill(skill.id)
    message.success(t('skills.deleted'))
    if (detailSkill.value?.id === skill.id) showDetail.value = false
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.deleteFailed'))
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
    message.error(t('skills.folderMustContainSkillMd'))
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
    message.success(t('skills.folderUploadSuccess'))
    showUploadModal.value = false
    dragOver.value = false
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.uploadFailed'))
  } finally {
    loading.value = false
  }
}

// ── Folder / ZIP Drag & Drop ──

const dragOver = ref(false)
const showUploadModal = ref(false)
const uploadMode = ref<'folder' | 'zip'>('folder')

function onDragOver(e: DragEvent) {
  e.preventDefault()
  dragOver.value = true
}

function onDragLeave(e: DragEvent) {
  const related = e.relatedTarget as Node | null
  if (!related || !(e.currentTarget as HTMLElement).contains(related)) {
    dragOver.value = false
  }
}

function onFolderDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  const dt = e.dataTransfer
  if (!dt) return

  // Prefer recursive directory traversal: yields reliable relative paths on folder drop
  const items = dt.items as unknown as any[] | undefined
  if (items && items.length && typeof items[0].webkitGetAsEntry === 'function') {
    const entries: any[] = []
    for (const it of items) {
      if (it.kind === 'file') {
        const entry = it.webkitGetAsEntry()
        if (entry) entries.push(entry)
      }
    }
    if (entries.length) {
      collectDroppedEntries(entries).then((files) => {
        if (files.length) handleDroppedFolder(files)
      })
      return
    }
  }

  // Fallback: flat file list (relative paths may be empty on some browsers)
  if (dt.files && dt.files.length) {
    const files = Array.from(dt.files).map((f: File) => ({
      file: f,
      path: (f as any).webkitRelativePath || f.name,
    }))
    handleDroppedFolder(files)
  }
}

interface DroppedEntry { file: File; path: string }

// Recursively read dropped DataTransferItem entries (files + directories),
// preserving the folder structure as relative paths.
function collectDroppedEntries(
  entries: any[],
  collected: Map<string, DroppedEntry> = new Map(),
): Promise<DroppedEntry[]> {
  return new Promise((resolve) => {
    let pending = entries.length
    if (pending === 0) return resolve(Array.from(collected.values()))

    const visit = (entry: any, base: string) => {
      if (entry.isFile) {
        entry.file(
          (file: File) => {
            collected.set(base + file.name, { file, path: base + file.name })
            if (--pending === 0) resolve(Array.from(collected.values()))
          },
          () => { if (--pending === 0) resolve(Array.from(collected.values())) },
        )
      } else if (entry.isDirectory) {
        const reader = entry.createReader()
        const readBatch = () => {
          reader.readEntries((batch: any[]) => {
            if (batch.length === 0) {
              if (--pending === 0) resolve(Array.from(collected.values()))
              return
            }
            pending += batch.length
            for (const child of batch) visit(child, base + entry.name + '/')
            readBatch()
          }, () => { if (--pending === 0) resolve(Array.from(collected.values())) })
        }
        readBatch()
      } else {
        if (--pending === 0) resolve(Array.from(collected.values()))
      }
    }

    for (const entry of entries) visit(entry, '')
  })
}

function handleDroppedFolder(files: DroppedEntry[]) {
  const fileList = files.map((f) => f.file)
  const paths = files.map((f) => f.path)
  const hasSkillMd = paths.some((p) => p.toUpperCase().endsWith('SKILL.MD'))
  if (!hasSkillMd) {
    message.error(t('skills.folderMustContainSkillMd'))
    return
  }
  doFolderUpload(fileList, paths)
}

// Unified drop handler — routes to folder or ZIP handling based on active mode
function onDrop(e: DragEvent) {
  if (uploadMode.value === 'zip') onZipDrop(e)
  else onFolderDrop(e)
}

// Unified click handler — opens the matching native picker for the active mode
function triggerUpload() {
  if (uploadMode.value === 'zip') triggerZipUpload()
  else triggerFolderUpload()
}

// ── ZIP Drag & Drop ──

function triggerZipUpload() {
  zipInput.value?.click()
}

function handleZipChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length) submitZip(input.files[0])
  input.value = ''
}

function onZipDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  const dt = e.dataTransfer
  if (!dt || !dt.files || dt.files.length === 0) return
  const zips = Array.from(dt.files).filter((f: File) => f.name.toLowerCase().endsWith('.zip'))
  if (zips.length) submitZip(zips[0])
}

// Re-use the existing ZIP upload path (validates + closes modal on success)
function submitZip(file: File) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error(t('skills.pleaseUploadZip'))
    return
  }
  handleZipUpload({ file: { file } })
}

// ── ZIP Upload ──

async function handleZipUpload(options: any) {
  const file = options.file.file
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error(t('skills.pleaseUploadZip'))
    return
  }
  loading.value = true
  try {
    await uploadZip(file)
    message.success(t('skills.zipUploadSuccess'))
    showUploadModal.value = false
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.uploadFailed'))
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
    message.error(t('skills.folderMustContainSkillMd'))
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
    message.success(t('skills.folderReuploaded'))
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.reuploadFailed'))
  } finally {
    loading.value = false
  }
}

async function handleReuploadZip(skillId: string, options: any) {
  const file = options.file.file
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error(t('skills.pleaseUploadZip'))
    return
  }
  loading.value = true
  try {
    await reuploadZip(skillId, file)
    message.success(t('skills.zipReuploaded'))
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.reuploadFailed'))
  } finally {
    loading.value = false
  }
}

// ── Enable / Disable ──

async function handleToggle(skill: Skill) {
  try {
    await toggleSkill(skill.id)
    message.success(skill.is_active ? t('skills.skillDisabled') : t('skills.skillEnabled'))
    if (detailSkill.value?.id === skill.id) {
      detailSkill.value = { ...detailSkill.value, is_active: !detailSkill.value.is_active }
    }
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.operationFailed'))
  }
}

// ── Sync ──

async function handleSync() {
  loading.value = true
  try {
    const result = await syncSkills()
    message.success(t('skills.syncComplete', { added: result.added, updated: result.updated, deactivated: result.deactivated }))
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('skills.syncFailed'))
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
  <div class="page-container pm-flex">
    <PageHeader :title="t('skills.title')" :icon="Bulb">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NButton size="small" @click="handleSync">
          <template #icon><NIcon><Sync /></NIcon></template>
          {{ t('skills.sync') }}
        </NButton>
        <NButton size="small" @click="showUploadModal = true">
          <template #icon><NIcon><CloudUpload /></NIcon></template>
          {{ t('skills.upload') }}
        </NButton>
        <NButton size="small" type="primary" @click="openCreate">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('skills.createOnline') }}
        </NButton>
      </template>
    </PageHeader>

    <!-- Upload Modal -->
    <AppModal v-model:show="showUploadModal" :title="t('skills.uploadModalTitle')" size="detail">
      <div class="sk-upload-modal">
        <!-- Mode toggle: switch the drop zone between folder and ZIP -->
        <div class="sk-upload-modes">
          <button
            type="button"
            :class="['sk-mode-tab', { active: uploadMode === 'folder' }]"
            @click="uploadMode = 'folder'"
          >
            <NIcon size="16"><FolderOpen /></NIcon>
            {{ t('skills.uploadFolder') }}
          </button>
          <button
            type="button"
            :class="['sk-mode-tab', { active: uploadMode === 'zip' }]"
            @click="uploadMode = 'zip'"
          >
            <NIcon size="16"><Archive /></NIcon>
            {{ t('skills.uploadZip') }}
          </button>
        </div>

        <!-- Adaptive drag & drop zone (accepts folder or .zip based on mode) -->
        <div
          :class="['sk-dropzone', `mode-${uploadMode}`, { dragover: dragOver }]"
          @dragover="onDragOver"
          @dragleave="onDragLeave"
          @drop="onDrop"
          @click="triggerUpload"
          role="button"
          tabindex="0"
          :aria-label="uploadMode === 'zip' ? t('skills.dragDropZipTitle') : t('skills.dragDropTitle')"
          @keydown.enter.prevent="triggerUpload"
          @keydown.space.prevent="triggerUpload"
        >
          <div class="sk-dropzone-content">
            <NIcon size="32" color="var(--color-primary)">
              <FolderOpen v-if="uploadMode === 'folder'" />
              <Archive v-else />
            </NIcon>
            <p>{{ uploadMode === 'zip' ? t('skills.dragDropZipTitle') : t('skills.dragDropTitle') }}</p>
            <span class="sk-dropzone-hint">
              {{ uploadMode === 'zip' ? t('skills.dragDropZipHint') : t('skills.dragDropHint') }}
            </span>
          </div>
        </div>
      </div>
    </AppModal>

    <!-- Hidden folder inputs -->
    <input ref="folderInput" type="file" style="display:none" @change="handleFolderChange" />
    <input ref="reuploadFolderInput" type="file" style="display:none" @change="handleReuploadFolderChange" />
    <input ref="zipInput" type="file" accept=".zip" style="display:none" @change="handleZipChange" />

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" :placeholder="t('skills.searchPlaceholder')" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterActive" :options="activeOptions" :placeholder="t('common.status')" size="small" style="width:130px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>{{ t('common.reset') }}</NButton>
    </div>

    <NSpin :show="loading" class="pm-scroll">
      <NEmpty v-if="!loading && skills.length === 0" :description="t('skills.empty')" />
      <div class="sk-list" v-if="skills.length > 0">
        <AppCard
          v-for="skill in skills"
          :key="skill.id"
          class="sk-card"
          :disabled="!skill.is_active"
          role="button"
          tabindex="0"
          @click="openDetail(skill)"
          @keydown.enter.prevent="openDetail(skill)"
          @keydown.space.prevent="openDetail(skill)"
        >
          <div class="sk-card-header">
            <div class="sk-card-title-wrap">
              <span class="sk-name" :title="skill.name">{{ skill.name }}</span>
              <NTag v-if="!skill.is_active" size="tiny" :bordered="false" type="default" class="sk-disabled-tag">{{ t('common.disabled') }}</NTag>
            </div>
            <div class="sk-card-toggle" @click.stop>
              <StatusToggle
                :value="skill.is_active"
                @update:value="() => handleToggle(skill)"
              />
            </div>
          </div>

          <p class="sk-card-desc" :title="skill.description ?? undefined">{{ skill.description || t('skills.noDescription') }}</p>

          <div class="sk-card-mcp">
            <span class="sk-card-label">{{ t('skills.mcpService') }}</span>
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
            <span v-else class="sk-meta-muted">{{ t('skills.none') }}</span>
          </div>

          <div class="sk-card-meta">
            <span class="sk-meta-muted">📁 {{ skill.folder_name }}</span>
            <span class="sk-meta-sep">·</span>
            <span class="sk-meta-muted">{{ t('skills.updated') }} {{ formatTime(skill.updated_at) }}</span>
          </div>
        </AppCard>
      </div>
    </NSpin>

    <AppPagination always-show :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />

    <!-- Create Modal -->
    <AppModal v-model:show="showCreateModal" :title="t('skills.createModalTitle')" size="detail">
      <NForm :model="createForm" label-placement="left" label-width="100">
        <NFormItem :label="t('common.name')" required>
          <NInput v-model:value="createForm.name" :placeholder="t('skills.namePlaceholder')" maxlength="200" />
        </NFormItem>
        <NFormItem :label="t('skills.description')">
          <NInput v-model:value="createForm.description" type="textarea"
            :placeholder="t('skills.descriptionPlaceholder')" rows="2" maxlength="250" />
        </NFormItem>
        <NFormItem :label="t('skills.mcpService')">
          <NSelect v-model:value="createForm.mcp_servers" :options="serverOptions" multiple
            :placeholder="t('skills.mcpServicePlaceholder')" />
        </NFormItem>
        <NFormItem :label="t('skills.skillBody')">
          <NInput v-model:value="createForm.body" type="textarea"
            :placeholder="t('skills.skillBodyPlaceholder')" rows="8" />
        </NFormItem>
        <NFormItem :label="t('common.enable')">
          <NSwitch v-model:value="createForm.is_active" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreateModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" :disabled="!createForm.name" @click="handleCreate">{{ t('common.create') }}</NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Edit SKILL.md Modal -->
    <AppModal v-model:show="showEditModal" :title="t('skills.editSkillMd')" size="code">
      <div style="margin-bottom:8px">
        <NText depth="3" style="font-size:12px">
          {{ t('skills.editSkillMdHint') }}
        </NText>
      </div>
      <NInput v-model:value="skillMdContent" type="textarea"
        :autosize="{ minRows: 14, maxRows: 18 }"
        style="font-family: monospace; font-size: 13px"
        :placeholder="t('skills.skillBodyTemplate')" />
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" @click="handleSaveEdit">{{ t('common.save') }}</NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Detail Modal -->
    <AppModal
      v-model:show="showDetail"
      :title="t('skills.detailTitle')"
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
          <NDescriptionsItem :label="t('common.name')">
            <span class="sk-detail-name">{{ detailSkill.name }}</span>
            <NTag v-if="!detailSkill.is_active" size="tiny" :bordered="false" type="default" style="margin-left:8px">{{ t('common.disabled') }}</NTag>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('skills.description')">
            <span v-if="detailSkill.description">{{ detailSkill.description }}</span>
            <span v-else class="sk-meta-muted">{{ t('skills.noDescription') }}</span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('skills.folder')">{{ detailSkill.folder_name }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('skills.mcpService')">
            <template v-if="detailSkill.mcp_servers && detailSkill.mcp_servers.length">
              <NSpace :size="6">
                <NTag v-for="s in detailSkill.mcp_servers" :key="s" size="tiny" type="info" :bordered="false">{{ s }}</NTag>
              </NSpace>
            </template>
            <span v-else class="sk-meta-muted">{{ t('skills.none') }}</span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('skills.apiKey')">
            <NSpace vertical :size="8" style="width: 100%">
              <NTag :type="apiKeyConfigured ? 'success' : 'default'" size="small" :bordered="false">
                {{ apiKeyConfigured ? t('skills.apiKeyActive') : t('skills.apiKeyVanilla') }}
              </NTag>
              <NSpace :size="8">
                <NInput
                  v-model:value="apiKeyInput"
                  type="password"
                  show-password-on="click"
                  :placeholder="t('skills.apiKeyPlaceholder')"
                  style="width: 260px"
                  :disabled="apiKeySaving"
                />
                <NButton
                  size="small"
                  type="primary"
                  :loading="apiKeySaving"
                  :disabled="!apiKeyInput"
                  @click="saveApiKey"
                >{{ t('skills.apiKeySave') }}</NButton>
                <NButton
                  size="small"
                  :disabled="!apiKeyConfigured || apiKeySaving"
                  @click="clearApiKey"
                >{{ t('skills.apiKeyClear') }}</NButton>
              </NSpace>
            </NSpace>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('common.status')">
            <NTag :type="detailSkill.is_active ? 'success' : 'default'" size="small">
              {{ detailSkill.is_active ? t('common.enabled') : t('common.disabled') }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('common.createdAt')">{{ formatTime(detailSkill.created_at) }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('common.updatedAt')">{{ formatTime(detailSkill.updated_at) }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('skills.skillId')">
            <span class="sk-id">{{ detailSkill.id }}</span>
          </NDescriptionsItem>
        </NDescriptions>
      </NSpin>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailSkill" @click="openEdit(detailSkill)">
            <template #icon><NIcon size="16"><Create /></NIcon></template>
            {{ t('common.edit') }}
          </NButton>
          <NButton size="small" v-if="detailSkill" @click="triggerReuploadFolder(detailSkill.id)">
            <template #icon><NIcon size="16"><CloudUpload /></NIcon></template>
            {{ t('skills.reupload') }}
          </NButton>
          <NUpload
            v-if="detailSkill"
            :show-file-list="false"
            :custom-request="(o: any) => handleReuploadZip(detailSkill!.id, o)"
          >
            <NButton size="small">
              <template #icon><NIcon size="16"><CloudUpload /></NIcon></template>
              {{ t('skills.reuploadZip') }}
            </NButton>
          </NUpload>
          <NButton size="small" v-if="detailSkill" @click="handleToggle(detailSkill)" :style="detailSkill.is_active
            ? { '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b', '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706', '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706' }
            : { '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e', '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a', '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a' }">
            <template #icon>
              <NIcon size="16">
                <Ban v-if="detailSkill.is_active" />
                <CheckmarkCircle v-else />
              </NIcon>
            </template>
            {{ detailSkill.is_active ? t('common.disable') : t('common.enable') }}
          </NButton>
          <NPopconfirm v-if="detailSkill" @positive-click="handleDelete(detailSkill)">
            <template #trigger>
              <NButton size="small" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                <template #icon><NIcon size="16"><Trash /></NIcon></template>
                {{ t('common.delete') }}
              </NButton>
            </template>
            {{ t('skills.confirmDeleteSkill') }}
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>

  </div>
</template>

<style scoped>
/* Skill card grid */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }

/* Folder drag & drop zone */
.sk-dropzone {
  border: 2px dashed var(--color-card-border);
  border-radius: 12px;
  padding: 26px;
  text-align: center;
  cursor: pointer;
  margin-bottom: 16px;
  background: var(--color-card-bg);
  transition: border-color .2s ease, background .2s ease, box-shadow .2s ease, transform .2s ease;
}
.sk-dropzone:hover,
.sk-dropzone.dragover {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  box-shadow: var(--shadow-sm);
}
.sk-dropzone.dragover {
  transform: scale(1.01);
}
.sk-dropzone:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.sk-dropzone-content p {
  margin: 10px 0 4px;
  font-weight: 600;
  font-size: var(--text-sm);
  color: var(--color-text);
}
.sk-dropzone-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

/* Mode toggle (folder / zip) */
.sk-upload-modes {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.sk-mode-tab {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 12px;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  border-radius: 10px;
  cursor: pointer;
  transition: color .2s ease, border-color .2s ease, background .2s ease, box-shadow .2s ease;
}
.sk-mode-tab:hover {
  color: var(--color-text);
  border-color: var(--color-primary);
}
.sk-mode-tab.active {
  color: var(--color-primary);
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  box-shadow: 0 0 0 1px var(--color-primary-soft);
}
.sk-mode-tab:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}

/* Upload modal */
.sk-upload-modal {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.sk-dropzone {
  margin-bottom: 0;
}
.sk-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  padding-top: 2px; /* prevent hover border-top clipping from overflow:auto parent */
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
