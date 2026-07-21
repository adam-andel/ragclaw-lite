<script setup lang="ts">
import { ref, computed, h, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NDataTable, NCard, NSpace, NButton, NIcon, NTag, NModal, NInput, NSelect,
  NProgress, NEmpty, NSpin, NBreadcrumb, NBreadcrumbItem, NPopconfirm, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  FolderOpen, Folder, DocumentText, Pencil, Trash,
  ArrowUp, Add, CloudUpload, Search,
} from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import type { WorkspaceEntry } from '@/api/workspace'
import {
  listWorkspace, downloadWorkspace, mkdirWorkspace,
  uploadWorkspace, renameWorkspace, deleteWorkspace, fileToBase64, triggerDownload,
} from '@/api/workspace'

const { t } = useI18n()
const message = useMessage()

// ── State ──
const currentPath = ref('')            // relative path inside the user sandbox root
const entries = ref<WorkspaceEntry[]>([])
const loading = ref(false)
const checkedRowKeys = ref<string[]>([])

// ── Filename search (server-side, recursive within currentPath) ──
const search = ref('')
function onSearch() {
  load()
}
function resetFilters() {
  search.value = ''
  filterType.value = 'all'
  load()
}

// ── File-type filter (client-side by extension) ──
const filterType = ref<string>('all')
// Common office document formats first, then other frequently used types.
const typeOptions = [
  { label: t('common.allTypes'), value: 'all' },
  { label: 'Word (.doc/.docx)', value: 'doc,docx' },
  { label: 'Excel (.xls/.xlsx)', value: 'xls,xlsx' },
  { label: 'PowerPoint (.ppt/.pptx)', value: 'ppt,pptx' },
  { label: 'PDF (.pdf)', value: 'pdf' },
  { label: 'Text (.txt)', value: 'txt' },
  { label: 'CSV (.csv)', value: 'csv' },
  { label: 'Markdown (.md)', value: 'md' },
  { label: 'Image (.png/.jpg/.gif/.webp/.svg)', value: 'png,jpg,jpeg,gif,webp,bmp,svg' },
  { label: 'Archive (.zip/.rar/.7z/.tar/.gz)', value: 'zip,rar,7z,tar,gz' },
  { label: 'JSON (.json)', value: 'json' },
]

function extOf(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx > 0 ? name.slice(idx + 1).toLowerCase() : ''
}

const showFolderModal = ref(false)
const newFolderName = ref('')

const renameTarget = ref<WorkspaceEntry | null>(null)
const renameName = ref('')

// ── Derived ──
const breadcrumbs = computed(() => {
  const segs = currentPath.value ? currentPath.value.split('/').filter(Boolean) : []
  const items = [{ label: t('workspace.breadcrumbRoot'), path: '' }]
  let acc = ''
  for (const s of segs) {
    acc = acc ? `${acc}/${s}` : s
    items.push({ label: s, path: acc })
  }
  return items
})

const sortedEntries = computed(() => {
  let list = entries.value
  if (filterType.value !== 'all') {
    const exts = filterType.value.split(',')
    // Only files are filtered by type; folders are hidden when a type is active.
    list = list.filter(e => e.type !== 'dir' && exts.includes(extOf(e.name)))
  }
  return [...list].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    return a.name.localeCompare(b.name)
  })
})

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let val = bytes / 1024
  let i = 0
  while (val >= 1024 && i < units.length - 1) { val /= 1024; i++ }
  return `${val.toFixed(1)} ${units[i]}`
}

function formatDate(mtime: number): string {
  return new Date(mtime * 1000).toLocaleString()
}

// ── Columns ──
const columns: DataTableColumns<WorkspaceEntry> = [
  {
    type: 'selection',
    multiple: true,
  },
  {
    title: t('workspace.columns.name'),
    key: 'name',
    minWidth: 220,
    render: (row) => h(
      'span',
      {
        style: 'display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-weight:600',
        onClick: () => openEntry(row),
      },
      [
        h(NIcon, { size: 18, color: row.type === 'dir' ? 'var(--color-primary)' : 'var(--color-text-muted)' },
          { default: () => h(row.type === 'dir' ? Folder : DocumentText) }),
        h('span', row.name),
      ],
    ),
  },
  {
    title: t('workspace.columns.type'),
    key: 'type',
    width: 110,
    render: (row) => h(
      NTag,
      { size: 'small', type: row.type === 'dir' ? 'primary' : 'default', bordered: false },
      { default: () => t(row.type === 'dir' ? 'workspace.dir' : 'workspace.file') },
    ),
  },
  {
    title: t('workspace.columns.size'),
    key: 'size',
    width: 130,
    render: (row) => formatSize(row.size),
  },
  {
    title: t('workspace.columns.modified'),
    key: 'mtime',
    width: 200,
    render: (row) => formatDate(row.mtime),
  },
  {
    title: t('workspace.columns.actions'),
    key: 'actions',
    width: 200,
    render: (row) => h(NSpace, { size: 4, wrap: false }, {
      default: () => [
        h(NButton, {
          size: 'tiny', quaternary: true,
          onClick: () => openEntry(row),
        }, { default: () => t(row.type === 'dir' ? 'workspace.open' : 'workspace.download') }),
        h(NButton, {
          size: 'tiny', quaternary: true,
          onClick: () => startRename(row),
        }, { icon: () => h(NIcon, { size: 14 }, { default: () => h(Pencil) }) }),
        h(NPopconfirm, {
          onPositiveClick: () => doDelete(row),
        }, {
          trigger: () => h(NButton, {
            size: 'tiny', quaternary: true, type: 'error',
          }, { icon: () => h(NIcon, { size: 14 }, { default: () => h(Trash) }) }),
          default: () => t('workspace.deleteWarning', { name: row.name }),
        }),
      ],
    }),
  },
]

// ── Actions ──
async function load() {
  loading.value = true
  try {
    const data = await listWorkspace(currentPath.value, search.value.trim())
    entries.value = data.entries || []
    checkedRowKeys.value = []
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.load'))
  } finally {
    loading.value = false
  }
}

function navigateTo(path: string) {
  currentPath.value = path
  search.value = ''
  load()
}

function openEntry(row: WorkspaceEntry) {
  if (row.type === 'dir') navigateTo(row.rel_path)
  else downloadEntry(row)
}

async function downloadEntry(row: WorkspaceEntry) {
  try {
    const blob = await downloadWorkspace(row.rel_path)
    triggerDownload(blob, row.name)
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.download'))
  }
}

// Folder creation
function openFolderModal() {
  newFolderName.value = ''
  showFolderModal.value = true
}
async function confirmFolder() {
  const name = newFolderName.value.trim()
  if (!name) { message.warning(t('workspace.nameEmpty')); return false }
  const full = currentPath.value ? `${currentPath.value}/${name}` : name
  try {
    await mkdirWorkspace(full)
    message.success(t('workspace.newFolder') + ' ✓')
    showFolderModal.value = false
    load()
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.create'))
  }
}

// ── Upload modal (mirrors DocumentManage.vue upload modal) ──
const showUploadModal = ref(false)
const uploadItems = ref<UploadFileItem[]>([])
const uploadRunning = ref(false)
const uploadPaused = ref(false)
const dragOver = ref(false)
const MAX_CONCURRENT_UPLOADS = 3

interface UploadFileItem {
  id: string
  name: string
  size: number
  progress: number
  status: 'pending' | 'uploading' | 'success' | 'error' | 'cancelled'
  error?: string
  file?: File
  controller?: AbortController
  timestamp: number
}

function openUploadModal() {
  showUploadModal.value = true
}

// ── File drop ──
function onDragOver(e: DragEvent) { e.preventDefault(); dragOver.value = true }
function onDragLeave() { dragOver.value = false }
function onDrop(e: DragEvent) {
  e.preventDefault(); dragOver.value = false
  if (e.dataTransfer?.files) addFiles(e.dataTransfer.files)
}

function triggerFileSelect() {
  const input = document.createElement('input')
  input.type = 'file'; input.multiple = true
  input.onchange = (e: Event) => {
    const files = (e.target as HTMLInputElement).files
    if (files) addFiles(files)
  }
  input.click()
}

function addFiles(fileList: FileList) {
  const maxSize = 50 * 1024 * 1024
  for (let i = 0; i < fileList.length; i++) {
    const f = fileList[i]
    if (f.size > maxSize) {
      message.warning(t('workspace.fileTooLarge', { name: f.name, size: (f.size / 1024 / 1024).toFixed(1) }))
      continue
    }
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
    uploadItems.value.push({
      id, name: f.name, size: f.size, progress: 0,
      status: 'pending', file: f, timestamp: Date.now(),
    })
  }
  // Auto-start: newly queued files upload immediately (concurrency-limited pool),
  // respecting an active pause. If a run is already in progress, idle workers pick them up.
  if (!uploadPaused.value && uploadItems.value.some(i => i.status === 'pending')) startUploads()
}

function clearUploadItems() {
  uploadItems.value.forEach(i => { if (i.controller) i.controller.abort() })
  uploadItems.value = []
}

async function uploadOne(item: UploadFileItem) {
  const controller = new AbortController()
  item.controller = controller
  try {
    const full = currentPath.value ? `${currentPath.value}/${item.name}` : item.name
    item.progress = 10
    const b64 = await fileToBase64(item.file!)
    item.progress = 60
    // uploadWorkspace has no abort support; cancel is best-effort only.
    await uploadWorkspace(full, b64)
    item.status = 'success'
    item.progress = 100
    load()
  } catch (e: any) {
    if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') {
      item.status = 'cancelled'
    } else {
      item.status = 'error'
      item.error = e?.message || t('workspace.errors.upload')
    }
  } finally {
    item.controller = undefined
  }
}

async function startUploads() {
  if (uploadRunning.value) return
  if (!uploadItems.value.some(i => i.status === 'pending')) return
  uploadRunning.value = true

  // Pool workers each atomically claim the next pending item (status flips to
  // 'uploading' synchronously before awaiting), so newly added files are picked
  // up by whichever worker becomes free. When paused, workers poll without
  // claiming so in-flight uploads finish and the pool resumes on unpause.
  // At most MAX_CONCURRENT_UPLOADS run at once.
  async function worker() {
    while (true) {
      const item = uploadItems.value.find(i => i.status === 'pending')
      if (!item) break
      if (uploadPaused.value) {
        // No in-flight uploads while paused: the pool can wind down instead of
        // polling forever. Resuming re-spawns the pool via resumeUploads().
        if (!uploadItems.value.some(i => i.status === 'uploading')) break
        await new Promise(r => setTimeout(r, 300))
        continue
      }
      item.status = 'uploading'
      item.progress = 0
      await uploadOne(item)
    }
  }

  const poolSize = Math.min(MAX_CONCURRENT_UPLOADS, uploadItems.value.length)
  await Promise.all(Array.from({ length: poolSize }, () => worker()))

  uploadRunning.value = false
  if (!uploadItems.value.some(i => i.status === 'pending' || i.status === 'uploading')) {
    message.success(t('workspace.uploadComplete'))
  }
}

function pauseUploads() {
  uploadPaused.value = true
}
function resumeUploads() {
  uploadPaused.value = false
  if (!uploadRunning.value) startUploads()
}
function clearSuccessItems() {
  uploadItems.value = uploadItems.value.filter(i => i.status !== 'success')
}

function cancelUpload(itemId: string) {
  const item = uploadItems.value.find(i => i.id === itemId)
  if (item?.controller) {
    item.controller.abort()
  } else if (item?.status === 'pending') {
    item.status = 'cancelled'
  }
}

const pendingCount = computed(() => uploadItems.value.filter(i => i.status === 'pending' || i.status === 'uploading').length)
const hasActiveUploads = computed(() => uploadRunning.value || uploadItems.value.some(i => i.status === 'uploading'))

// Footer button is "Pause All" / "Resume" while uploads run, or a "Start Upload"
// fallback for pending items that were added but not yet started.
const buttonLabel = computed(() => {
  if (uploadPaused.value) return t('workspace.resume')
  if (uploadRunning.value) return t('workspace.pause')
  return t('workspace.startUpload')
})
function onUploadButtonClick() {
  if (uploadPaused.value) resumeUploads()
  else if (uploadRunning.value) pauseUploads()
  else startUploads()
}

// Clean successfully uploaded items when the upload modal is closed.
watch(showUploadModal, (open) => {
  if (!open) clearSuccessItems()
})

// Rename
function startRename(row: WorkspaceEntry) {
  renameTarget.value = row
  renameName.value = row.name
}
async function confirmRename() {
  const row = renameTarget.value
  if (!row) return
  const name = renameName.value.trim()
  if (!name || name === row.name) { renameTarget.value = null; return false }
  try {
    await renameWorkspace(row.rel_path, name)
    message.success(t('workspace.rename') + ' ✓')
    renameTarget.value = null
    load()
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.rename'))
  }
}

// Delete
async function doDelete(row: WorkspaceEntry) {
  try {
    await deleteWorkspace(row.rel_path)
    message.success(t('workspace.delete') + ' ✓')
    load()
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.delete'))
  }
}

onMounted(load)
</script>

<template>
  <div class="workspace-view">
    <PageHeader :title="t('workspace.title')" :icon="FolderOpen">
      <template #actions>
        <NButton size="small" @click="openFolderModal">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('workspace.newFolder') }}
        </NButton>
        <NButton size="small" type="primary" @click="openUploadModal">
          <template #icon><NIcon><CloudUpload /></NIcon></template>
          {{ t('workspace.upload') }}
        </NButton>
      </template>
    </PageHeader>

    <div class="ws-breadcrumb-row">
      <NBreadcrumb class="ws-breadcrumb">
        <NBreadcrumbItem
          v-for="(item, idx) in breadcrumbs"
          :key="item.path || 'root'"
          :clickable="idx !== breadcrumbs.length - 1"
          @click="idx !== breadcrumbs.length - 1 && navigateTo(item.path)"
        >
          {{ item.label }}
        </NBreadcrumbItem>
      </NBreadcrumb>
      <span
        class="ws-breadcrumb-back"
        :class="{ 'ws-breadcrumb-back--disabled': breadcrumbs.length <= 1 }"
        @click="breadcrumbs.length > 1 && navigateTo(breadcrumbs[breadcrumbs.length - 2].path)"
      >
        <NIcon><ArrowUp /></NIcon>
        {{ t('workspace.back') }}
      </span>
    </div>

    <div class="ws-search-row">
      <NInput
        v-model:value="search"
        :placeholder="t('common.searchFilename')"
        clearable
        size="small"
        style="flex:1"
        @keyup.enter="onSearch"
        @clear="onSearch"
      >
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect
        v-model:value="filterType"
        :options="typeOptions"
        :placeholder="t('common.type')"
        size="small"
        style="width:220px"
      />
      <NButton size="small" secondary @click="resetFilters">{{ t('common.reset') }}</NButton>
    </div>

    <NCard class="ws-card" :bordered="false">
      <NSpin :show="loading">
        <NEmpty
          v-if="!loading && sortedEntries.length === 0"
          :description="t('workspace.empty')"
          class="ws-empty"
        >
          <template #extra>
            <span class="ws-empty-hint">{{ t('workspace.emptyHint') }}</span>
          </template>
        </NEmpty>

        <NDataTable
          v-else
          v-model:checked-row-keys="checkedRowKeys"
          :columns="columns"
          :data="sortedEntries"
          :row-key="(row: WorkspaceEntry) => row.rel_path"
          :bordered="false"
          size="small"
          class="ws-table"
        />
      </NSpin>
    </NCard>

    <!-- New folder -->
    <NModal
      v-model:show="showFolderModal"
      preset="dialog"
      :title="t('workspace.newFolder')"
      :positive-text="t('workspace.confirm')"
      :negative-text="t('workspace.cancel')"
      @positive-click="confirmFolder"
    >
      <NInput v-model:value="newFolderName" :placeholder="t('workspace.folderName')" @keydown.enter="confirmFolder" />
    </NModal>

    <!-- Rename -->
    <NModal
      :show="!!renameTarget"
      preset="dialog"
      :title="t('workspace.rename')"
      :positive-text="t('workspace.confirm')"
      :negative-text="t('workspace.cancel')"
      @positive-click="confirmRename"
      @negative-click="renameTarget = null"
      @close="renameTarget = null"
    >
      <NInput v-model:value="renameName" :placeholder="t('workspace.newName')" @keydown.enter="confirmRename" />
    </NModal>

    <!-- Upload Modal (mirrors DocumentManage.vue upload modal) -->
    <AppModal v-model:show="showUploadModal" :title="t('workspace.uploadFile')" size="detail">
      <div class="upload-modal-body">
        <!-- Drop zone -->
        <div :class="['upload-zone', { dragover: dragOver }]"
          @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop"
          @click="triggerFileSelect"
        >
          <div class="upload-zone-content">
            <NIcon size="36" color="var(--color-primary)"><CloudUpload /></NIcon>
            <p>{{ t('workspace.uploadHint') }}</p>
            <span class="upload-hint">{{ t('workspace.dropFilesHere') }}</span>
          </div>
        </div>

        <!-- Per-file queue -->
        <div v-if="uploadItems.length > 0" class="upload-queue">
          <div class="upload-queue-header">
            <span>{{ t('workspace.fileCount', { count: uploadItems.length }) }}</span>
            <NButton size="small" @click="clearUploadItems" :disabled="hasActiveUploads">{{ t('workspace.clearCompleted') }}</NButton>
          </div>
          <div v-for="item in uploadItems" :key="item.id" class="upload-file-row">
            <div class="upload-file-info">
              <span class="upload-file-name">📄 {{ item.name }}</span>
              <span class="upload-file-size">{{ formatSize(item.size) }}</span>
              <NTag :type="item.status === 'success' ? 'success' : item.status === 'error' ? 'error' : item.status === 'cancelled' ? 'warning' : item.status === 'uploading' ? 'info' : 'default'" size="tiny" :bordered="false">
                {{ item.status === 'pending' ? t('workspace.uploadStatus.waiting') : item.status === 'uploading' ? t('workspace.uploadStatus.uploading') : item.status === 'success' ? t('workspace.uploadStatus.complete') : item.status === 'error' ? t('workspace.uploadStatus.failed') : t('workspace.uploadStatus.cancelled') }}
              </NTag>
              <NButton
                v-if="item.status === 'pending' || item.status === 'uploading'"
                size="tiny" text type="error"
                @click="cancelUpload(item.id)"
              >{{ t('common.cancel') }}</NButton>
            </div>
            <NProgress
              v-if="item.status === 'uploading'"
              type="line"
              :percentage="item.progress"
              :height="6"
              :border-radius="3"
              style="flex:1; min-width:80px"
            />
            <span v-if="item.status === 'error' && item.error" class="upload-file-error">{{ item.error }}</span>
          </div>
        </div>
      </div>

      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" :disabled="pendingCount === 0" @click="onUploadButtonClick">
            {{ buttonLabel }}
          </NButton>
        </NSpace>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.workspace-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.ws-breadcrumb-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ws-breadcrumb {
  padding: 0 4px 12px;
  flex-shrink: 0;
}
.ws-breadcrumb-back {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 4px 12px;
  color: var(--color-primary, #18a058);
  cursor: pointer;
  font-size: 14px;
  transition: opacity 0.2s;
}
.ws-breadcrumb-back:hover {
  opacity: 0.75;
}
.ws-breadcrumb-back--disabled {
  color: var(--color-text-muted, #999);
  cursor: not-allowed;
  pointer-events: none;
}
.ws-search-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px 12px;
}
.ws-card {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.ws-table {
  --n-th-text-color: var(--color-text-muted);
  --n-td-text-color: var(--color-text);
}
.ws-empty {
  padding: 64px 0;
}
.ws-empty-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

/* Upload modal (mirrors DocumentManage.vue) */
.upload-modal-body { display: flex; flex-direction: column; gap: 12px; max-height: 50vh; overflow-y: auto; }
.upload-zone { border: 2px dashed var(--color-border); border-radius: 8px; padding: 28px; text-align: center; cursor: pointer; transition: all .2s; }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--color-primary); background: rgba(59,130,246,0.04); }
.upload-zone-content p { margin: 10px 0 4px; font-weight: 500; }
.upload-hint { font-size: 0.8rem; color: var(--color-text-muted); }
.upload-queue { display: flex; flex-direction: column; gap: 8px; }
.upload-queue-header { display: flex; align-items: center; justify-content: space-between; font-size: 0.85rem; font-weight: 600; }
.upload-file-row { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; border: 1px solid var(--color-border); border-radius: 6px; }
.upload-file-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.upload-file-name { font-weight: 600; }
.upload-file-size { font-size: 0.78rem; color: var(--color-text-muted); }
.upload-file-error { font-size: 0.75rem; color: var(--color-danger, #ef4444); }
</style>
