<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NSpace, NSpin, NEmpty, NProgress,
  NInput, NSelect, NPagination, NPopconfirm, useMessage,
  NIcon, NModal, NCard, NDescriptions, NDescriptionsItem,
  NCheckbox, NTooltip,
} from 'naive-ui'
import { CloudUpload, Search, DocumentText, Add, Create, Chatbubbles, People, Trash } from '@vicons/ionicons5'
import {
  uploadDocument, listAllDocuments,
  getDocumentStatus, getDocumentChunks, deleteDocument,
  listKnowledgeBases, createKnowledgeBase, getSupportedTypes, downloadDocument,
  updateKnowledgeBase, deleteKnowledgeBase, addDocumentsToKB,
} from '@/api/documents'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { DocumentItem, ChunkItem, KnowledgeBase } from '@/types'

const message = useMessage()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// List state
const docs = ref<DocumentItem[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(15)
const loading = ref(false)
const search = ref('')
const filterStatus = ref<string | null>(null)
const filterType = ref<string | null>(null)
const filterKbId = ref<string | null>(null)
const showKbFilter = ref(false)

const filterKbName = computed(() => {
  const kb = allKbs.value.find(k => k.id === filterKbId.value)
  return kb?.name || ''
})

const selectedKb = computed<KnowledgeBase | undefined>(() =>
  allKbs.value.find(k => k.id === filterKbId.value)
)

const filterKbDesc = computed(() => selectedKb.value?.description || '')

// Supported extensions — loaded from /documents/supported-types at mount time
const supportedExts = ref<string[]>([])

// Create KB modal
const showCreateKb = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const creating = ref(false)

// Upload modal
const showUploadModal = ref(false)
const uploadTargetKb = ref<string | null>(null)

const UPLOAD_STORAGE_KEY = 'erag:upload:items'
const UPLOAD_TTL_MS = 24 * 60 * 60 * 1000

interface UploadFileItem {
  id: string
  name: string
  size: number
  progress: number
  status: 'pending' | 'uploading' | 'success' | 'error' | 'cancelled'
  error?: string
  docId?: string
  file?: File
  controller?: AbortController
  timestamp: number
}

const uploadItems = ref<UploadFileItem[]>([])
const uploadRunning = ref(false)
const dragOver = ref(false)

function loadUploadItems() {
  try {
    const raw = localStorage.getItem(UPLOAD_STORAGE_KEY)
    if (!raw) return
    const items: UploadFileItem[] = JSON.parse(raw)
    const now = Date.now()
    uploadItems.value = items.filter(item => {
      if (now - item.timestamp > UPLOAD_TTL_MS) return false
      if (item.status === 'uploading') {
        item.status = 'cancelled'
        item.error = '页面关闭导致上传中断'
      }
      return item.status !== 'success'
    })
  } catch { /* ignore */ }
}

function saveUploadItems() {
  const toStore = uploadItems.value.map(({ id, name, size, progress, status, error, timestamp }) => ({
    id, name, size, progress, status, error: error || undefined, timestamp,
  }))
  try { localStorage.setItem(UPLOAD_STORAGE_KEY, JSON.stringify(toStore)) } catch { /* quota */ }
}

// Chunks modal
const showChunks = ref(false)
const chunks = ref<ChunkItem[]>([])
const chunksLoading = ref(false)
const chunksPerPage = 10
const chunkSearch = ref('')
const chunkPage = ref(1)
const expandedChunks = ref(new Set<string>())

// KB modal
const allKbs = ref<any[]>([])
const showDocKbs = ref(false)
const docKbSearchText = ref('')
const selectedDocKbIds = ref<string[]>([])
const kbFilterSearch = ref('')
const kbFilterSortBy = ref<'recent' | 'doc_count'>('recent')

const kbSortOptions = [
  { label: '最近更新', value: 'recent' },
  { label: '文档数量', value: 'doc_count' },
]

const filteredKbsForFilter = computed(() => {
  let list = [...allKbs.value]
  if (kbFilterSearch.value.trim()) {
    const q = kbFilterSearch.value.trim().toLowerCase()
    list = list.filter(kb =>
      kb.name.toLowerCase().includes(q) ||
      (kb.description && kb.description.toLowerCase().includes(q))
    )
  }
  list.sort((a, b) => {
    if (kbFilterSortBy.value === 'doc_count') {
      return b.doc_count - a.doc_count
    }
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  })
  return list
})

// KB action modals
const showRenameKb = ref(false)
const renameKbId = ref('')
const renameKbName = ref('')
const renameKbDesc = ref('')
const renaming = ref(false)

const showShare = ref(false)
const shareKbId = ref('')
const shareUsers = ref<any[]>([])
const shareAddUser = ref('')
const allUsers = ref<any[]>([])
const shareLoading = ref(false)

const showSelectDocs = ref(false)
const availableDocs = ref<DocumentItem[]>([])
const selectedDocIds = ref<string[]>([])
const loadingAvailableDocs = ref(false)
const availableTotal = ref(0)
const availablePage = ref(1)
const availableSearch = ref('')
const availableStatus = ref<string | null>('completed')
const availableType = ref<string | null>(null)
const linkingDocs = ref(false)

// Detail modal
const detailDoc = ref<DocumentItem | null>(null)
const showDetail = ref(false)

function openDetail(doc: DocumentItem) {
  detailDoc.value = doc
  showDetail.value = true
}

function closeDetail() {
  showDetail.value = false
  detailDoc.value = null
}

async function deleteDetailDoc() {
  if (!detailDoc.value) return
  await handleDelete(detailDoc.value.id)
  closeDetail()
}

const filteredDocKbs = computed(() => {
  if (!docKbSearchText.value.trim()) {
    return allKbs.value.filter(kb => selectedDocKbIds.value.includes(kb.id))
  }
  const q = docKbSearchText.value.trim().toLowerCase()
  return allKbs.value.filter(kb =>
    selectedDocKbIds.value.includes(kb.id) && kb.name.toLowerCase().includes(q)
  )
})

const filteredChunks = computed(() => {
  if (!chunkSearch.value.trim()) return chunks.value
  const q = chunkSearch.value.trim().toLowerCase()
  return chunks.value.filter(c => c.content.toLowerCase().includes(q))
})

const paginatedChunks = computed(() => {
  const start = (chunkPage.value - 1) * chunksPerPage
  return filteredChunks.value.slice(start, start + chunksPerPage)
})

const totalChunkPages = computed(() => Math.max(1, Math.ceil(filteredChunks.value.length / chunksPerPage)))

function toggleChunkExpand(id: string) {
  const s = new Set(expandedChunks.value)
  if (s.has(id)) s.delete(id); else s.add(id)
  expandedChunks.value = s
}

// Progress polling
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  loadSupportedTypes()
  loadUploadItems()
  startPolling()
  await loadKBs()
  const qkb = route.query.kb
  if (typeof qkb === 'string' && allKbs.value.some(k => k.id === qkb)) {
    filterKbId.value = qkb
  }
  await loadDocs()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function loadDocs() {
  loading.value = true
  try {
    const params: any = { page: page.value, size: size.value }
    if (search.value) params.search = search.value
    if (filterStatus.value) {
      if (filterStatus.value === 'unlinked') {
        params.unlinked = true
      } else {
        params.status = filterStatus.value
      }
    }
    if (filterType.value) params.file_type = filterType.value
    if (filterKbId.value) params.kb_id = filterKbId.value
    const res = await listAllDocuments(params)
    docs.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    message.error('加载文档失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function selectKb(kbId: string | null) {
  filterKbId.value = kbId
  page.value = 1
  showKbFilter.value = false
  const query = { ...route.query }
  if (kbId) query.kb = kbId
  else delete query.kb
  router.replace({ query })
  loadDocs()
}

function goToChat(kbId: string) {
  router.push({ path: '/chat', query: { kb: kbId } })
}

function blurActive() {
  (document.activeElement as HTMLElement | null)?.blur()
}

function openRenameKb(kb: KnowledgeBase) {
  renameKbId.value = kb.id
  renameKbName.value = kb.name
  renameKbDesc.value = kb.description || ''
  showRenameKb.value = true
}

async function handleRenameKb() {
  if (!renameKbName.value.trim()) return
  renaming.value = true
  try {
    await updateKnowledgeBase(renameKbId.value, {
      name: renameKbName.value,
      description: renameKbDesc.value || undefined,
    })
    await loadKBs()
    showRenameKb.value = false
    message.success('知识库已更新')
  } catch (e: any) {
    message.error('更新失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    renaming.value = false
  }
}

async function handleDeleteKb(id: string) {
  try {
    await deleteKnowledgeBase(id)
    if (filterKbId.value === id) {
      selectKb(null)
    }
    await loadKBs()
    message.success('知识库已删除')
  } catch (e: any) {
    message.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

const allUserOptions = computed(() =>
  allUsers.value
    .filter((u: any) => !shareUsers.value.some((s: any) => s.id === u.id))
    .map((u: any) => ({ label: `${u.display_name || u.username} (${u.username})`, value: u.id }))
)

async function openShare(kbId: string) {
  shareKbId.value = kbId
  shareLoading.value = true
  showShare.value = true
  try {
    const r = await client.get(`/kb/${kbId}/users`)
    shareUsers.value = r.data
  } catch { shareUsers.value = [] }
  try {
    const r = await client.get('/users')
    allUsers.value = r.data
  } catch { allUsers.value = [] }
  shareLoading.value = false
}

async function addKbUser(uid: string) {
  if (!uid) return
  try {
    await client.post(`/kb/${shareKbId.value}/users/${uid}`)
    const r = await client.get(`/kb/${shareKbId.value}/users`)
    shareUsers.value = r.data
    shareAddUser.value = ''
    message.success('已添加共享用户')
  } catch (e: any) {
    message.error('添加失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function removeKbUser(uid: string) {
  try {
    await client.delete(`/kb/${shareKbId.value}/users/${uid}`)
    const r = await client.get(`/kb/${shareKbId.value}/users`)
    shareUsers.value = r.data
    message.success('已移除共享用户')
  } catch (e: any) {
    message.error('移除失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function openSelectDocs(kbId: string) {
  showSelectDocs.value = true
  selectedDocIds.value = []
  availablePage.value = 1
  availableSearch.value = ''
  availableStatus.value = 'completed'
  availableType.value = null
  await loadAvailableDocs(kbId)
}

async function loadAvailableDocs(kbId?: string) {
  loadingAvailableDocs.value = true
  try {
    const params: any = { page: availablePage.value, size: 20 }
    if (availableSearch.value) params.search = availableSearch.value
    if (availableStatus.value) params.status = availableStatus.value
    if (availableType.value) params.file_type = availableType.value
    const res = await listAllDocuments(params)
    availableDocs.value = res.data.items
    availableTotal.value = res.data.total
  } catch (e: any) {
    message.error('加载文档失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loadingAvailableDocs.value = false
  }
}

async function handleSelectDocs() {
  if (selectedDocIds.value.length === 0 || !filterKbId.value) return
  linkingDocs.value = true
  try {
    const res = await addDocumentsToKB(filterKbId.value, selectedDocIds.value)
    message.success(`已添加 ${res.data.added} 个文档${res.data.skipped > 0 ? '，跳过 ' + res.data.skipped + ' 个' : ''}`)
    showSelectDocs.value = false
    await loadKBs()
    await loadDocs()
  } catch (e: any) {
    message.error('添加失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    linkingDocs.value = false
  }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    const processing = docs.value.filter(d =>
      ['pending', 'parsing', 'chunking', 'embedding'].includes(d.status)
    )
    if (processing.length === 0) return
    for (const doc of processing) {
      try {
        const res = await getDocumentStatus(doc.id)
        Object.assign(doc, {
          status: res.data.status,
          progress: res.data.progress,
          error_message: res.data.error_message,
          chunk_count: res.data.chunk_count,
        })
      } catch { /* ignore */ }
    }
  }, 3000)
}

function onPageChange(p: number) { page.value = p; loadDocs() }
function onSearch() { page.value = 1; loadDocs() }

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
  input.accept = supportedExts.value.map(e => `.${e}`).join(',')
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
      message.warning(`文件过大：${f.name} (${(f.size / 1024 / 1024).toFixed(1)}MB)`)
      continue
    }
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
    uploadItems.value.push({
      id, name: f.name, size: f.size, progress: 0,
      status: 'pending', file: f, timestamp: Date.now(),
    })
  }
  saveUploadItems()
}

function removeUploadItem(itemId: string) {
  const item = uploadItems.value.find(i => i.id === itemId)
  if (item?.controller) item.controller.abort()
  uploadItems.value = uploadItems.value.filter(i => i.id !== itemId)
  saveUploadItems()
}

function clearUploadItems() {
  uploadItems.value.forEach(i => { if (i.controller) i.controller.abort() })
  uploadItems.value = []
  saveUploadItems()
}

function openUploadModal() {
  uploadTargetKb.value = filterKbId.value
  showUploadModal.value = true
}

async function startUploads() {
  const pending = uploadItems.value.filter(i => i.status === 'pending')
  if (pending.length === 0) return
  uploadRunning.value = true
  for (const item of pending) {
    if (item.status !== 'pending') continue
    item.status = 'uploading'
    item.progress = 0
    saveUploadItems()

    const controller = new AbortController()
    item.controller = controller

    try {
      const res = await uploadDocument(item.file!, (pct) => { item.progress = pct; saveUploadItems() }, controller.signal, uploadTargetKb.value || undefined)
      item.status = 'success'
      item.progress = 100
      item.docId = res.data.id
    } catch (e: any) {
      if (e?.name === 'CanceledError' || e?.code === 'ERR_CANCELED') {
        item.status = 'cancelled'
      } else {
        item.status = 'error'
        item.error = e?.response?.data?.detail || e.message
      }
    } finally {
      item.controller = undefined
      saveUploadItems()
    }
  }
  uploadRunning.value = false
  message.success('上传完成')
  setTimeout(() => {
    uploadItems.value = uploadItems.value.filter(i => i.status !== 'success')
    saveUploadItems()
  }, 5000)
  await loadDocs()
  await loadKBs()
}

function cancelUpload(itemId: string) {
  const item = uploadItems.value.find(i => i.id === itemId)
  if (item?.controller) {
    item.controller.abort()
  } else if (item?.status === 'pending') {
    item.status = 'cancelled'
    saveUploadItems()
  }
}

const pendingCount = computed(() => uploadItems.value.filter(i => i.status === 'pending' || i.status === 'uploading').length)
const hasActiveUploads = computed(() => uploadRunning.value || uploadItems.value.some(i => i.status === 'uploading'))

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

async function handleDelete(id: string) {
  try {
    await deleteDocument(id)
    docs.value = docs.value.filter(d => d.id !== id)
    total.value -= 1
    message.success('文档已删除')
  } catch (e: any) {
    message.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function handleDownload(doc: DocumentItem) {
  try {
    const res = await downloadDocument(doc.id)
    const blob = new Blob([res.data])
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = doc.filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  } catch (e: any) {
    message.error('下载失败：' + (e?.message || '未知错误'))
  }
}

async function openChunks(docId: string) {
  chunksLoading.value = true
  chunkSearch.value = ''
  chunkPage.value = 1
  expandedChunks.value = new Set()
  showChunks.value = true
  try {
    const res = await getDocumentChunks(docId)
    chunks.value = res.data
  } catch {
    message.error('加载分块失败')
    showChunks.value = false
  } finally {
    chunksLoading.value = false
  }
}

// ── Helpers ──

const statusColors: Record<string, string> = {
  pending: 'default', uploaded: 'default',
  parsing: 'warning', chunking: 'warning',
  embedding: 'info', completed: 'success', failed: 'error',
}
const statusLabels: Record<string, string> = {
  pending: '等待中', uploaded: '已上传',
  parsing: '解析中', chunking: '分块中',
  embedding: '向量化中', completed: '已完成', failed: '失败',
}

// File type → icon + color (covers all 14 supported formats)
const fileTypeConfig: Record<string, { icon: string; color: string; label: string }> = {
  pdf: { icon: '📕', color: '#ef4444', label: 'PDF' },
  docx: { icon: '📘', color: '#3b82f6', label: 'Word' },
  md: { icon: '📗', color: '#22c55e', label: 'MD' },
  markdown: { icon: '📗', color: '#22c55e', label: 'MD' },
  txt: { icon: '📄', color: '#64748b', label: 'TXT' },
  csv: { icon: '📊', color: '#f59e0b', label: 'CSV' },
  json: { icon: '🗂️', color: '#8b5cf6', label: 'JSON' },
  xlsx: { icon: '📈', color: '#22c55e', label: 'Excel' },
  xls: { icon: '📈', color: '#22c55e', label: 'Excel' },
  pptx: { icon: '📙', color: '#f97316', label: 'PPT' },
  html: { icon: '🌐', color: '#0ea5e9', label: 'HTML' },
  htm: { icon: '🌐', color: '#0ea5e9', label: 'HTML' },
  eml: { icon: '✉️', color: '#0891b2', label: 'EML' },
  msg: { icon: '📧', color: '#0891b2', label: 'MSG' },
  rtf: { icon: '📄', color: '#7c3aed', label: 'RTF' },
  epub: { icon: '📖', color: '#16a34a', label: 'EPUB' },
  ipynb: { icon: '📓', color: '#f97316', label: 'Notebook' },
}

function getFileTypeConfig(ext: string) {
  return fileTypeConfig[ext.toLowerCase()] || { icon: '📄', color: '#64748b', label: ext.toUpperCase() }
}

const typeOptions = computed(() => {
  const opts: { label: string; value: string | null }[] = [{ label: '全部类型', value: null }]
  for (const ext of supportedExts.value) {
    // Avoid duplicate entries for multi-ext parsers (e.g. md + markdown)
    if (!opts.some(o => o.value === ext)) {
      opts.push({ label: getFileTypeConfig(ext).label, value: ext })
    }
  }
  return opts
})

// Build the upload-zone hint text from the live supported-extensions list,
// so disabling a plugin via /admin/plugins immediately reflects here.
const supportedFormatsHint = computed(() => {
  if (supportedExts.value.length === 0) return '加载支持格式中…，单文件最大 50MB'
  const labels = Array.from(new Set(
    supportedExts.value.map(ext => getFileTypeConfig(ext).label)
  ))
  return `支持 ${labels.join('、')}，单文件最大 50MB`
})

const statusOptions = [
  { label: '全部状态', value: null },
  { label: '已完成', value: 'completed' }, { label: '处理中', value: 'pending' },
  { label: '等待中', value: 'pending' }, { label: '失败', value: 'failed' },
  { label: '未关联', value: 'unlinked' },
]

const availableStatusOptions = [
  { label: '全部状态', value: null },
  { label: '已完成', value: 'completed' },
  { label: '处理中', value: 'pending' },
  { label: '失败', value: 'failed' },
]

const processingStatuses = ['pending', 'parsing', 'chunking', 'embedding']

function isProcessing(status: string) {
  return processingStatuses.includes(status)
}

async function loadKBs() {
  try {
    const res = await listKnowledgeBases()
    allKbs.value = res.data
  } catch { allKbs.value = [] }
}

function openDocKbs(kbIds: string[]) {
  if (kbIds.length === 0) return
  selectedDocKbIds.value = kbIds
  docKbSearchText.value = ''
  showDocKbs.value = true
}

function goToKb(kbId: string) {
  showDocKbs.value = false
  router.push({ path: '/knowledge', query: { kb: kbId } })
}

async function handleCreateKb() {
  if (!newKbName.value.trim()) return
  creating.value = true
  try {
    await createKnowledgeBase({ name: newKbName.value.trim(), description: newKbDesc.value.trim() || undefined })
    message.success('知识库创建成功')
    showCreateKb.value = false
    newKbName.value = ''
    newKbDesc.value = ''
    await loadKBs()
  } catch (e: any) {
    message.error('创建失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

async function loadSupportedTypes() {
  try {
    const data = await getSupportedTypes()
    supportedExts.value = data.extensions
  } catch {
    // Fallback to a minimal safe set so uploads still work if the endpoint is unreachable
    supportedExts.value = ['pdf', 'docx', 'md', 'txt']
  }
}
</script>
<template>
  <div class="dm-view">
    <div class="dm-header">
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><DocumentText /></NIcon>
        <h2>文档管理</h2>
        <span v-if="total > 0" class="kb-header-badge">{{ total }}</span>
      </div>
      <div class="dm-header-actions">
        <NButton type="primary" @click="showCreateKb = true">
          <template #icon><NIcon><Create /></NIcon></template>
          新建知识库
        </NButton>
        <NButton type="primary" @click="openUploadModal">
          <template #icon><NIcon><Add /></NIcon></template>
          上传文件
        </NButton>
      </div>
    </div>

    <!-- Create KB Modal -->
    <NModal v-model:show="showCreateKb" preset="card" title="新建知识库" style="max-width: 440px;">
      <div class="create-kb-body">
        <NInput v-model:value="newKbName" placeholder="知识库名称" />
        <NInput v-model:value="newKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreateKb = false">取消</NButton>
          <NButton type="primary" :loading="creating" :disabled="!newKbName.trim()" @click="handleCreateKb">创建</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Upload Modal -->
    <NModal v-model:show="showUploadModal" preset="card" title="上传文件"
      style="width: 90vw; max-width: 560px"
    >
      <div class="upload-modal-body">
        <!-- Knowledge base selector -->
        <div class="upload-kb-select">
          <span class="upload-kb-label">关联知识库</span>
          <NSelect
            v-model:value="uploadTargetKb"
            :options="[{ label: '不关联（仅上传）', value: null }, ...allKbs.map((kb: any) => ({ label: kb.name, value: kb.id }))]"
            placeholder="选择知识库（可选）"
            size="small"
            clearable
            style="flex:1"
          />
        </div>

        <!-- Drop zone -->
        <div :class="['upload-zone', { dragover: dragOver }]"
          @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop"
          @click="triggerFileSelect"
        >
          <div class="upload-zone-content">
            <NIcon size="36" color="var(--color-primary)"><CloudUpload /></NIcon>
            <p>点击或拖拽文件到此处上传</p>
            <span class="upload-hint">{{ supportedFormatsHint }}</span>
          </div>
        </div>

        <!-- Per-file queue -->
        <div v-if="uploadItems.length > 0" class="upload-queue">
          <div class="upload-queue-header">
            <span>{{ uploadItems.length }} 个文件</span>
            <NButton size="small" @click="clearUploadItems" :disabled="hasActiveUploads">清空已完成</NButton>
          </div>
          <div v-for="item in uploadItems" :key="item.id" class="upload-file-row">
            <div class="upload-file-info">
              <span class="upload-file-name">📄 {{ item.name }}</span>
              <span class="upload-file-size">{{ formatSize(item.size) }}</span>
              <NTag :type="item.status === 'success' ? 'success' : item.status === 'error' ? 'error' : item.status === 'cancelled' ? 'warning' : item.status === 'uploading' ? 'info' : 'default'" size="tiny" :bordered="false">
                {{ item.status === 'pending' ? '等待' : item.status === 'uploading' ? '上传中' : item.status === 'success' ? '完成' : item.status === 'error' ? '失败' : '已取消' }}
              </NTag>
              <NButton
                v-if="item.status === 'pending' || item.status === 'uploading'"
                size="tiny" text type="error"
                @click="cancelUpload(item.id)"
              >取消</NButton>
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
          <NButton @click="showUploadModal = false">关闭</NButton>
          <NButton type="primary" :loading="hasActiveUploads" :disabled="pendingCount === 0" @click="startUploads">
            {{ hasActiveUploads ? '上传中…' : pendingCount > 0 ? `开始上传 (${pendingCount})` : '开始上传' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- KB Filter -->
    <div class="dm-kb-filter">
      <span class="dm-kb-label">当前知识库</span>
      <div class="dm-kb-panel">
        <div class="dm-kb-top">
          <NButton secondary @click="showKbFilter = true">
            <template #icon><NIcon><DocumentText /></NIcon></template>
            {{ filterKbId ? filterKbName : '全部' }}
          </NButton>
          <span v-if="filterKbDesc" class="dm-kb-desc">{{ filterKbDesc }}</span>
        </div>
        <div v-if="selectedKb" class="dm-kb-bottom">
          <span class="dm-kb-count">📄 {{ selectedKb.doc_count }} 文档</span>
          <span class="dm-kb-count">🧬 {{ selectedKb.vector_count }} 分片</span>
          <NSpace class="dm-kb-actions" size="small">
            <NButton size="small" @click="openRenameKb(selectedKb); blurActive()">
              <template #icon><NIcon size="14"><Create /></NIcon></template>
              修改描述
            </NButton>
            <NButton size="small" @click="goToChat(selectedKb.id); blurActive()">
              <template #icon><NIcon size="14"><Chatbubbles /></NIcon></template>
              发起对话
            </NButton>
            <NButton v-if="auth.isStaff" size="small" @click="openShare(selectedKb.id); blurActive()">
              <template #icon><NIcon size="14"><People /></NIcon></template>
              共享
            </NButton>
            <NButton size="small" @click="openSelectDocs(selectedKb.id); blurActive()">
              <template #icon><NIcon size="14"><Search /></NIcon></template>
              添加文档
            </NButton>
            <NPopconfirm @positive-click="handleDeleteKb(selectedKb.id)">
              <template #trigger>
                <NTooltip trigger="hover">
                  <template #trigger>
                    <NButton size="small" type="error" @click="blurActive()">
                      <template #icon><NIcon size="14"><Trash /></NIcon></template>
                      删除
                    </NButton>
                  </template>
                  删除知识库不会删除关联文档
                </NTooltip>
              </template>
              确定删除「{{ selectedKb.name }}」？文档不会被删除，仅解除关联。
            </NPopconfirm>
          </NSpace>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" placeholder="搜索文件名…" clearable @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NSelect v-model:value="filterStatus" :options="statusOptions" placeholder="状态" style="width:120px" @update:value="onSearch" />
      <NSelect v-model:value="filterType" :options="typeOptions" placeholder="类型" style="width:120px" @update:value="onSearch" />
      <NButton @click="onSearch" secondary>筛选</NButton>
    </div>

    <!-- Doc List -->
    <NSpin :show="loading">
      <NEmpty v-if="!loading && docs.length === 0" description="暂无文档，请上传" />
      <div class="dm-list" v-if="docs.length > 0">
        <NCard
          v-for="doc in docs"
          :key="doc.id"
          size="small"
          class="dm-card"
          hoverable
          role="button"
          tabindex="0"
          @click="openDetail(doc)"
          @keydown.enter.prevent="openDetail(doc)"
          @keydown.space.prevent="openDetail(doc)"
        >
          <div class="doc-card-header">
            <span class="doc-type-icon doc-card-icon" :style="{ color: getFileTypeConfig(doc.file_type).color }">
              {{ getFileTypeConfig(doc.file_type).icon }}
            </span>
            <div class="doc-card-title-wrap">
              <span class="doc-name" :title="doc.filename">{{ doc.filename }}</span>
            </div>
          </div>
          <div class="doc-card-meta">
            <span>{{ doc.chunk_count }} 分块</span>
            <span class="doc-meta-sep">·</span>
            <span>关联{{ doc.kb_ids.length }} 个知识库</span>
            <span class="doc-meta-sep">·</span>
            <span>{{ formatSize(doc.file_size) }}</span>
            <span class="doc-meta-sep">·</span>
            <span class="doc-meta-muted">{{ new Date(doc.created_at).toLocaleDateString('zh-CN') }}</span>
          </div>
          <div v-if="isProcessing(doc.status)" class="doc-card-progress">
            <NProgress
              type="line"
              :percentage="doc.progress"
              :height="6"
              :border-radius="3"
              :color="statusColors[doc.status] === 'warning' ? '#f59e0b' : '#4f6ef7'"
              :rail-color="'var(--color-border)'"
              style="flex:1; min-width:100px"
            />
            <span class="doc-progress-text">{{ doc.progress }}%</span>
          </div>
        </NCard>
      </div>
    </NSpin>

    <div class="dm-pagination" v-if="total > size">
      <NPagination :page="page" :page-size="size" :item-count="total" @update:page="onPageChange" />
    </div>

    <!-- Chunks Modal -->
    <NModal v-model:show="showChunks" preset="card" title="分块预览"
      style="width: 90vw; max-width: 720px"
    >
      <div class="chunks-modal">
        <NInput
          v-if="chunks.length > 0"
          v-model:value="chunkSearch"
          placeholder="搜索分块内容…"
          size="small"
          clearable
          @update:value="chunkPage = 1"
          style="margin-bottom:12px"
        >
          <template #prefix><NIcon size="15"><Search /></NIcon></template>
        </NInput>

        <NSpin :show="chunksLoading">
          <NEmpty v-if="!chunksLoading && chunks.length === 0" description="暂无分块数据" />
          <NEmpty v-if="!chunksLoading && chunks.length > 0 && filteredChunks.length === 0" description="无匹配的分块" />

          <div v-if="filteredChunks.length > 0">
            <div class="chunk-count">共 {{ filteredChunks.length }} 个分块</div>
            <NCard v-for="c in paginatedChunks" :key="c.id" size="small" class="chunk-card">
              <div class="chunk-meta">
                <NTag size="tiny">#{{ c.chunk_index }}</NTag>
                <NTag size="tiny" v-if="c.heading">{{ c.heading }}</NTag>
                <span class="chunk-meta-tokens">{{ c.token_count }} tokens</span>
              </div>
              <div
                :class="['chunk-content', { expanded: expandedChunks.has(c.id) }]"
                @click="toggleChunkExpand(c.id)"
                role="button"
                tabindex="0"
                :aria-expanded="expandedChunks.has(c.id)"
                @keydown.enter.prevent="toggleChunkExpand(c.id)"
                @keydown.space.prevent="toggleChunkExpand(c.id)"
              >
                <p>{{ c.content }}</p>
              </div>
              <NButton text size="tiny" class="chunk-expand-btn" @click="toggleChunkExpand(c.id)">
                {{ expandedChunks.has(c.id) ? '收起' : '展开' }}
              </NButton>
            </NCard>

            <div v-if="totalChunkPages > 1" class="chunk-pagination">
              <NButton size="tiny" :disabled="chunkPage <= 1" @click="chunkPage--">上一页</NButton>
              <span class="chunk-page-indicator">{{ chunkPage }} / {{ totalChunkPages }}</span>
              <NButton size="tiny" :disabled="chunkPage >= totalChunkPages" @click="chunkPage++">下一页</NButton>
            </div>
          </div>

          <NButton @click="showChunks = false" block style="margin-top:12px">关闭</NButton>
        </NSpin>
      </div>
    </NModal>

    <!-- Doc KBs Modal -->
    <NModal v-model:show="showDocKbs" preset="card" title="关联知识库"
      style="width: 90vw; max-width: 480px"
      @after-leave="docKbSearchText = ''"
    >
      <NInput
        v-if="allKbs.length > 0"
        v-model:value="docKbSearchText"
        placeholder="搜索知识库名称..."
        clearable
        style="margin-bottom:12px"
      >
        <template #prefix><NIcon size="15"><Search /></NIcon></template>
      </NInput>
      <template v-if="filteredDocKbs.length > 0">
        <div class="picker-scroll">
          <NCard
            v-for="kb in filteredDocKbs"
            :key="kb.id"
            size="small"
            class="kb-pick-card"
            role="button"
            tabindex="0"
            @click="goToKb(kb.id)"
            @keydown.enter.prevent="goToKb(kb.id)"
            @keydown.space.prevent="goToKb(kb.id)"
          >
            <strong>{{ kb.name }}</strong>
            <span v-if="kb.description" class="kb-pick-desc">{{ kb.description }}</span>
            <span class="kb-pick-meta">{{ kb.doc_count }} 文档 · {{ kb.vector_count }} 向量</span>
          </NCard>
        </div>
      </template>
      <NEmpty v-else description="没有匹配的知识库" style="padding:16px 0" />
    </NModal>

    <!-- Document Detail Modal -->
    <NModal v-model:show="showDetail" preset="card" :title="detailDoc?.filename || '文档详情'"
      style="width: 90vw; max-width: 560px"
      @after-leave="detailDoc = null"
    >
      <div v-if="detailDoc">
        <NDescriptions bordered :column="1" size="small" label-placement="left" label-style="width: 120px">
          <NDescriptionsItem label="文件名">{{ detailDoc.filename }}</NDescriptionsItem>
          <NDescriptionsItem label="文件类型">
            {{ getFileTypeConfig(detailDoc.file_type).label }} ({{ detailDoc.file_type }})
          </NDescriptionsItem>
          <NDescriptionsItem label="文件大小">{{ formatSize(detailDoc.file_size) }}</NDescriptionsItem>
          <NDescriptionsItem label="状态">
            <NTag :type="statusColors[detailDoc.status] as any" size="small">
              {{ statusLabels[detailDoc.status] || detailDoc.status }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem v-if="detailDoc.status === 'failed' && detailDoc.error_message" label="错误信息">
            {{ detailDoc.error_message }}
          </NDescriptionsItem>
          <NDescriptionsItem label="分块数">
            <span
              v-if="detailDoc.chunk_count > 0"
              class="doc-kb-link"
              @click="openChunks(detailDoc.id)"
              role="button"
              tabindex="0"
              @keydown.enter.prevent="openChunks(detailDoc.id)"
              @keydown.space.prevent="openChunks(detailDoc.id)"
            >{{ detailDoc.chunk_count }} 分块</span>
            <span v-else>0</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="关联知识库">
            <span
              :class="detailDoc.kb_ids.length > 0 ? 'doc-kb-link' : 'doc-meta-muted'"
              @click="openDocKbs(detailDoc.kb_ids)"
              role="button"
              tabindex="0"
              @keydown.enter.prevent="openDocKbs(detailDoc.kb_ids)"
              @keydown.space.prevent="openDocKbs(detailDoc.kb_ids)"
            >
              {{ detailDoc.kb_ids.length > 0 ? `关联${detailDoc.kb_ids.length} 个知识库` : '未关联知识库' }}
            </span>
          </NDescriptionsItem>
          <NDescriptionsItem label="创建时间">{{ new Date(detailDoc.created_at).toLocaleString('zh-CN') }}</NDescriptionsItem>
          <NDescriptionsItem v-if="detailDoc.updated_at" label="更新时间">{{ new Date(detailDoc.updated_at).toLocaleString('zh-CN') }}</NDescriptionsItem>
          <NDescriptionsItem label="文档 ID">{{ detailDoc.id }}</NDescriptionsItem>
        </NDescriptions>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDetail = false">关闭</NButton>
          <NButton v-if="detailDoc" @click="handleDownload(detailDoc)">下载原件</NButton>
          <NPopconfirm v-if="detailDoc" @positive-click="deleteDetailDoc">
            <template #trigger>
              <NButton type="error">删除</NButton>
            </template>
            确定删除文档「{{ detailDoc.filename }}」？将从所有知识库中移除。
          </NPopconfirm>
        </NSpace>
      </template>
    </NModal>

    <!-- KB Filter Modal -->
    <NModal v-model:show="showKbFilter" preset="card" title="选择知识库"
      style="width: 90vw; max-width: 720px"
      @after-leave="kbFilterSearch = ''; kbFilterSortBy = 'recent'"
    >
      <div class="kb-filter-toolbar">
        <NInput v-model:value="kbFilterSearch" placeholder="搜索知识库名称…" clearable style="flex:1">
          <template #prefix><NIcon size="15"><Search /></NIcon></template>
        </NInput>
        <NSelect v-model:value="kbFilterSortBy" :options="kbSortOptions" style="width: 140px" />
      </div>
      <div class="kb-filter-grid">
        <NCard
          size="small"
          class="kb-filter-card"
          :class="{ 'kb-filter-active': filterKbId === null }"
          role="button"
          tabindex="0"
          @click="selectKb(null)"
          @keydown.enter.prevent="selectKb(null)"
          @keydown.space.prevent="selectKb(null)"
        >
          <strong>全部</strong>
          <span class="kb-filter-count">共 {{ allKbs.length }} 个知识库</span>
          <span class="kb-filter-meta">显示所有文档</span>
        </NCard>
        <NCard
          v-for="kb in filteredKbsForFilter"
          :key="kb.id"
          size="small"
          class="kb-filter-card"
          :class="{ 'kb-filter-active': filterKbId === kb.id }"
          role="button"
          tabindex="0"
          @click="selectKb(kb.id)"
          @keydown.enter.prevent="selectKb(kb.id)"
          @keydown.space.prevent="selectKb(kb.id)"
        >
          <strong>{{ kb.name }}</strong>
          <span v-if="kb.description" class="kb-filter-desc">{{ kb.description }}</span>
          <span class="kb-filter-meta">{{ kb.doc_count }} 文档 · {{ kb.vector_count }} 分片</span>
        </NCard>
      </div>
      <NEmpty v-if="filteredKbsForFilter.length === 0" description="无匹配的知识库" />
    </NModal>

    <!-- Rename KB Modal -->
    <NModal v-model:show="showRenameKb" preset="card" title="编辑知识库"
      style="width: 90vw; max-width: 440px"
    >
      <div class="kb-form">
        <NInput v-model:value="renameKbName" placeholder="知识库名称" />
        <NInput v-model:value="renameKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
        <NButton type="primary" :loading="renaming" @click="handleRenameKb" block>保存</NButton>
      </div>
    </NModal>

    <!-- Share Modal -->
    <NModal v-model:show="showShare" preset="card" title="共享管理"
      style="width: 90vw; max-width: 640px"
    >
      <div class="share-form">
        <NSpin :show="shareLoading">
          <div class="share-add-row">
            <NSelect v-model:value="shareAddUser" :options="allUserOptions"
              placeholder="搜索用户…" filterable clearable style="flex:1"
            />
            <NButton type="primary" :disabled="!shareAddUser" @click="addKbUser(shareAddUser)">
              <template #icon><NIcon><Add /></NIcon></template>
              添加
            </NButton>
          </div>
          <div v-if="!shareLoading && shareUsers.length === 0" class="share-empty">
            <NEmpty description="暂无共享用户" />
          </div>
          <div class="share-list" v-if="shareUsers.length > 0">
            <div v-for="u in shareUsers" :key="u.id" class="share-row">
              <div class="share-user-info">
                <span class="share-user-avatar">👤</span>
                <div>
                  <div class="share-user-name">{{ u.display_name || u.username }}</div>
                  <div class="share-user-sub">{{ u.username }} · {{ u.role === 'admin' ? '管理员' : '普通用户' }}</div>
                </div>
              </div>
              <NButton text type="error" @click="removeKbUser(u.id)">移除</NButton>
            </div>
          </div>
        </NSpin>
      </div>
    </NModal>

    <!-- Select Documents Modal -->
    <NModal v-model:show="showSelectDocs" preset="card" title="选择文档加入知识库"
      style="width: 90vw; max-width: 720px"
    >
      <div class="select-docs-modal">
        <div class="select-docs-filters">
          <NInput v-model:value="availableSearch" placeholder="搜索文件名…" clearable @keyup.enter="loadAvailableDocs" style="flex:1">
            <template #prefix><NIcon><Search /></NIcon></template>
          </NInput>
          <NSelect v-model:value="availableStatus" :options="availableStatusOptions" placeholder="状态" style="width:110px" @update:value="loadAvailableDocs" />
          <NSelect v-model:value="availableType" :options="typeOptions" placeholder="类型" style="width:110px" @update:value="loadAvailableDocs" />
          <NButton @click="loadAvailableDocs" secondary>筛选</NButton>
        </div>
        <NSpin :show="loadingAvailableDocs">
          <div v-if="!loadingAvailableDocs && availableDocs.length === 0" class="select-docs-empty">
            <NEmpty description="还没有可添加的已完成文档" />
            <NButton type="primary" dashed @click="showSelectDocs = false; router.push('/documents')">
              <template #icon><NIcon><Add /></NIcon></template>
              前往文档管理页上传文档
            </NButton>
          </div>
          <div class="select-docs-list" v-if="availableDocs.length > 0">
            <div v-for="doc in availableDocs" :key="doc.id"
              :class="['select-doc-row', { selected: selectedDocIds.includes(doc.id) }]"
              @click="selectedDocIds.includes(doc.id)
                ? selectedDocIds = selectedDocIds.filter(id => id !== doc.id)
                : selectedDocIds.push(doc.id)"
            >
              <NCheckbox :checked="selectedDocIds.includes(doc.id)" style="flex-shrink:0" />
              <span class="select-doc-name">{{ getFileTypeConfig(doc.file_type).icon }} {{ doc.filename }}</span>
              <NSpace size="small">
                <NTag size="small">{{ doc.file_type.toUpperCase() }}</NTag>
                <NTag size="small">{{ formatSize(doc.file_size) }}</NTag>
                <NTag size="small" type="success">已完成</NTag>
              </NSpace>
            </div>
          </div>
        </NSpin>
        <div class="select-docs-actions">
          <div class="select-docs-left">
            <span class="select-docs-count">已选 {{ selectedDocIds.length }}{{ availableTotal ? ' / 共 ' + availableTotal + ' 个文档' : '' }}</span>
            <NButton text size="tiny" type="primary" @click="showSelectDocs = false; router.push('/documents')">
              上传更多文档 →
            </NButton>
          </div>
          <NSpace>
            <NButton @click="showSelectDocs = false">取消</NButton>
            <NButton type="primary" :disabled="selectedDocIds.length === 0" :loading="linkingDocs" @click="handleSelectDocs">
              加入知识库
            </NButton>
          </NSpace>
        </div>
      </div>
    </NModal>
  </div>
</template>
<style scoped>
.dm-view { height: 100%; overflow-y: auto; }
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
.dm-header .kb-header-title { display: flex; align-items: center; gap: 10px; }
.dm-header .kb-header-title h2 { font-size: var(--text-xl); font-weight: 700; }
.dm-header .kb-header-badge {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-soft);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-primary);
}
.dm-header-actions { display: flex; align-items: center; gap: 8px; }

/* Create KB Modal */
.create-kb-body { display: flex; flex-direction: column; gap: 12px; }

/* Upload Modal */
.upload-kb-select { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.upload-kb-label { font-size: var(--text-sm); font-weight: 500; white-space: nowrap; }
.upload-modal-body { display: flex; flex-direction: column; gap: 12px; max-height: 50vh; overflow-y: auto; }
.upload-zone { border: 2px dashed var(--color-border); border-radius: 8px; padding: 28px; text-align: center; cursor: pointer; transition: all .2s; }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--color-primary); background: rgba(88,166,255,0.04); }
.upload-zone-content p { margin: 10px 0 4px; font-weight: 500; }
.upload-hint { font-size: 0.8rem; color: var(--color-text-muted); }

.upload-queue { display: flex; flex-direction: column; gap: 8px; }
.upload-queue-header { display: flex; justify-content: space-between; align-items: center; font-weight: 500; font-size: var(--text-sm); }
.upload-file-row { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius); }
.upload-file-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.upload-file-name { font-size: var(--text-sm); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.upload-file-size { font-size: var(--text-xs); color: var(--color-text-muted); font-family: 'JetBrains Mono', monospace; }
.upload-file-error { font-size: var(--text-xs); color: var(--color-error); word-break: break-all; }

.dm-kb-filter { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.dm-kb-label { font-size: var(--text-sm); font-weight: 500; color: var(--color-text); line-height: 34px; flex-shrink: 0; }
.dm-kb-panel { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 0; }
.dm-kb-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dm-kb-desc { font-size: var(--text-sm); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.dm-kb-bottom { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: var(--text-sm); }
.dm-kb-bottom :deep(.n-button) { font-size: var(--text-sm); }
.dm-kb-bottom :deep(.n-button):focus:not(:hover):not(:active) { background: var(--color-surface); box-shadow: none; }
.dm-kb-bottom :deep(.n-button--error-type):focus:not(:hover):not(:active) { background: var(--color-surface); box-shadow: none; }
.dm-kb-count {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.dm-kb-actions { flex-wrap: wrap; }

/* Filters */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }

/* Doc list */
.dm-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.dm-card { cursor: pointer; transition: box-shadow .2s, border-color .2s; }
.dm-card:hover { box-shadow: var(--shadow-sm); }
.dm-card:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -1px; border-radius: var(--radius); }

.doc-card-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
}
.doc-card-icon { font-size: 1.6rem; }
.doc-card-title-wrap {
  flex: 1;
  min-width: 0;
}
.doc-card-title-wrap .doc-name {
  max-width: 100%;
  display: block;
}
.doc-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-wrap: wrap;
}
.doc-card-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.doc-row-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.doc-name-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
.doc-type-icon { font-size: 1.1rem; flex-shrink: 0; line-height: 1; }
.doc-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.doc-error-tag { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.doc-actions { display: flex; gap: 4px; flex-shrink: 0; }

.doc-row-bottom {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.doc-progress-text {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
  min-width: 2.5em;
  text-align: right;
  flex-shrink: 0;
}
.doc-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.doc-meta-label {
  font-weight: 600;
  color: var(--color-text);
}
.doc-meta-sep {
  color: var(--color-border);
  margin: 0 2px;
}
.doc-meta-muted { color: var(--color-text-muted); }
.doc-kb-link { color: var(--color-primary); cursor: pointer; }
.doc-kb-link:hover { text-decoration: underline; }
.doc-kb-link:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; border-radius: 2px; }
.doc-name-clickable { cursor: pointer; }
.doc-name-clickable:hover { text-decoration: underline; color: var(--color-primary); }
.doc-name-clickable:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; border-radius: 2px; }

.dm-pagination { display: flex; justify-content: center; margin-top: 16px; padding-bottom: 24px; }

/* Chunks */
.chunks-modal { display: flex; flex-direction: column; max-height: 75vh; overflow-y: auto; }
.chunk-count { font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 10px; }
.chunk-card { margin-bottom: 8px; }
.chunk-card:last-of-type { margin-bottom: 0; }
.chunk-meta { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.chunk-meta-tokens { font-size: var(--text-xs); color: var(--color-text-muted); }
.chunk-content {
  cursor: pointer;
  position: relative;
}
.chunk-content:not(.expanded) p {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.chunk-content.expanded p {
  display: block;
}
.chunk-content p {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: var(--text-sm);
  line-height: 1.6;
}
.chunk-content:not(.expanded)::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 32px;
  background: linear-gradient(transparent, var(--color-surface));
  pointer-events: none;
}
.chunk-expand-btn {
  margin-top: 4px;
  font-size: var(--text-xs);
}

.chunk-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}
.chunk-page-indicator {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
  min-width: 4em;
  text-align: center;
}

/* KB picker modal (shared) */
.picker-scroll { max-height: 55vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.kb-pick-card {
  cursor: pointer;
  transition: border-color .2s, box-shadow .2s;
  border-left: 3px solid transparent;
}
.kb-pick-card:hover { border-color: var(--color-primary); box-shadow: var(--shadow-sm); }
.kb-pick-active { border-left-color: var(--color-primary); background: var(--color-primary-soft); }
.kb-pick-card strong { display: block; font-size: var(--text-sm); margin-bottom: 2px; }
.kb-pick-desc { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-meta { font-size: 0.65rem; color: var(--color-text-muted); }

/* KB filter modal */
.kb-filter-toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.kb-filter-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  max-height: 55vh;
  overflow-y: auto;
}
.kb-filter-card {
  cursor: pointer;
  transition: border-color .2s, box-shadow .2s, background .2s;
  border: 1px solid var(--color-border);
}
.kb-filter-card:hover { border-color: var(--color-primary); box-shadow: var(--shadow-sm); }
.kb-filter-active { border-color: var(--color-primary); background: var(--color-primary-soft); }
.kb-filter-card strong { display: block; font-size: var(--text-sm); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-filter-desc { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-filter-count { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 2px; }
.kb-filter-meta { display: block; font-size: 0.65rem; color: var(--color-text-muted); }

/* KB action modals */
.kb-form { display: flex; flex-direction: column; gap: 12px; }
.share-form { display: flex; flex-direction: column; max-height: 60vh; }
.share-add-row { display: flex; gap: 8px; margin-bottom: 16px; }
.share-empty { padding: 20px 0; }
.share-list { flex: 1; overflow-y: auto; min-height: 0; }
.share-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 8px; border-bottom: 1px solid var(--color-border); transition: background .15s; }
.share-row:hover { background: rgba(88, 166, 255, 0.04); }
.share-user-info { display: flex; align-items: center; gap: 10px; }
.share-user-avatar { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--color-border); border-radius: 50%; font-size: 0.9rem; }
.share-user-name { font-weight: 500; font-size: 0.9rem; }
.share-user-sub { font-size: 0.75rem; color: var(--color-text-muted); }

.select-docs-modal { display: flex; flex-direction: column; gap: 12px; max-height: 70vh; }
.select-docs-filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.select-docs-list { max-height: 50vh; overflow-y: auto; }
.select-doc-row { display: flex; align-items: center; gap: 10px; padding: 10px 8px; cursor: pointer; border-bottom: 1px solid var(--color-border); transition: background .15s; }
.select-doc-row:hover { background: rgba(88, 166, 255, 0.04); }
.select-doc-row.selected { background: rgba(88, 166, 255, 0.1); }
.select-doc-name { font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.select-docs-actions { display: flex; justify-content: space-between; align-items: center; padding-top: 8px; border-top: 1px solid var(--color-border); }
.select-docs-left { display: flex; align-items: center; gap: 12px; }
.select-docs-empty { display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 24px 0; }
.select-docs-count { font-size: var(--text-sm); color: var(--color-text-muted); }

@media (max-width: 640px) {
  .kb-filter-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 420px) {
  .kb-filter-grid { grid-template-columns: 1fr; }
  .kb-filter-toolbar { flex-direction: column; }
  .kb-filter-toolbar :deep(.n-base-selection) { width: 100% !important; }
}
</style>