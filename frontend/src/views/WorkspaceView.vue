<script setup lang="ts">
import { ref, computed, h, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NDataTable, NCard, NSpace, NButton, NIcon, NTag, NModal, NInput,
  NEmpty, NSpin, NBreadcrumb, NBreadcrumbItem, NPopconfirm, useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  FolderOpen, Folder, DocumentText, Pencil, Trash,
  Download, ArrowUp, Add, CloudUpload,
} from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
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

const showFolderModal = ref(false)
const newFolderName = ref('')

const renameTarget = ref<WorkspaceEntry | null>(null)
const renameName = ref('')

const fileInput = ref<HTMLInputElement | null>(null)

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

const sortedEntries = computed(() =>
  [...entries.value].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    return a.name.localeCompare(b.name)
  }),
)

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
    const data = await listWorkspace(currentPath.value)
    entries.value = data.entries || []
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.load'))
  } finally {
    loading.value = false
  }
}

function navigateTo(path: string) {
  currentPath.value = path
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

// Upload
function triggerUpload() {
  fileInput.value?.click()
}
function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files || [])
  files.forEach(uploadFile)
  input.value = ''
}
async function uploadFile(file: File) {
  const full = currentPath.value ? `${currentPath.value}/${file.name}` : file.name
  try {
    const b64 = await fileToBase64(file)
    await uploadWorkspace(full, b64)
    message.success(file.name + ' ✓')
    load()
  } catch (e: any) {
    message.error(e?.message || t('workspace.errors.upload'))
  }
}

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
    <PageHeader :title="t('workspace.title')" :subtitle="t('workspace.subtitle')" :icon="FolderOpen">
      <template #actions>
        <NButton size="small" type="primary" @click="openFolderModal">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('workspace.newFolder') }}
        </NButton>
        <NButton size="small" @click="triggerUpload">
          <template #icon><NIcon><CloudUpload /></NIcon></template>
          {{ t('workspace.upload') }}
        </NButton>
        <input ref="fileInput" type="file" multiple style="display:none" @change="onFilePicked" />
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
      <NButton size="small" tertiary class="ws-breadcrumb-back" @click="navigateTo(breadcrumbs.length > 1 ? breadcrumbs[breadcrumbs.length - 2].path : '')" :disabled="breadcrumbs.length <= 1">
        <template #icon><NIcon><ArrowUp /></NIcon></template>
        {{ t('workspace.back') }}
      </NButton>
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
</style>
