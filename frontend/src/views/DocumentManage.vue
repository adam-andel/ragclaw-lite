<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NSpace, NSpin, NEmpty, NProgress,
  NInput, NSelect, NPopconfirm, useMessage,
  NIcon, NCard, NDescriptions, NDescriptionsItem,
  NCheckbox, NTooltip,
} from 'naive-ui'
import { CloudUpload, Search, DocumentText, Add, Create, Chatbubbles, People, Trash, Close, Remove } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  uploadDocument, listAllDocuments,
  getDocumentStatus, getDocumentChunks, deleteDocument,
  listKnowledgeBases, createKnowledgeBase, getSupportedTypes, downloadDocument,
  updateKnowledgeBase, deleteKnowledgeBase, addDocumentsToKB, removeDocumentFromKB,
} from '@/api/documents'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { DocumentItem, ChunkItem, KnowledgeBase } from '@/types'
import KbPickerModal from '@/components/kb/KbPickerModal.vue'
import { formatDateTime, formatDate } from '@/i18n/format'

const { t } = useI18n()
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
const filterStatus = ref<string>('all')
const filterType = ref<string>('all')
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

// KB form modal (create + edit share the same modal)
const showKbForm = ref(false)
const kbFormMode = ref<'create' | 'edit'>('create')
const kbFormId = ref('')
const kbFormName = ref('')
const kbFormDesc = ref('')
const kbFormPrompt = ref('')
const kbFormSaving = ref(false)

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
        item.error = t('documents.uploadInterruptedByClose')
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

// Chunks preview (shown inline inside the Document Detail Modal)
const showDetailChunks = ref(false)
const chunkPreviewTitle = ref<HTMLElement | null>(null)
const chunkDocId = ref<string>('')
const chunks = ref<ChunkItem[]>([])   // current page only (server-paginated)
const chunkTotal = ref(0)             // total matching chunks for current search
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
const kbFilterMode = ref<'filter' | 'upload'>('filter')

function openKbFilter(mode: 'filter' | 'upload' = 'filter') {
  kbFilterMode.value = mode
  showKbFilter.value = true
}

function onKbFilterSelect(kbId: string | null) {
  if (kbFilterMode.value === 'upload') {
    uploadTargetKb.value = kbId
    showKbFilter.value = false
  } else {
    selectKb(kbId)
  }
}

const uploadTargetKbName = computed(() => {
  if (!uploadTargetKb.value) return t('documents.notLinked')
  return allKbs.value.find(k => k.id === uploadTargetKb.value)?.name || t('documents.notLinked')
})

// KB action modals
const showShare = ref(false)
const shareKbId = ref('')
const shareUsers = ref<any[]>([])
const shareAddUser = ref('')
const allUsers = ref<any[]>([])
const shareLoading = ref(false)
const showAddMoreUsers = ref(false)
const shareUserSearch = ref('')
const shareUserPage = ref(1)
const shareUserPageSize = ref(9)
const shareAddedPage = ref(1)
const shareAddedPageSize = ref(9)

const showSelectDocs = ref(false)
const availableDocs = ref<DocumentItem[]>([])
const selectedDocIds = ref<string[]>([])
const loadingAvailableDocs = ref(false)
const availableTotal = ref(0)
const availablePage = ref(1)
const availablePageSize = ref(20)
const availableSearch = ref('')
const availableStatus = ref<string | null>('')
const availableType = ref<string>('all')
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

// Total pages is derived from the server-reported total (backend pagination)
const totalChunkPages = computed(() => Math.max(1, Math.ceil(chunkTotal.value / chunksPerPage)))

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
    if (filterStatus.value && filterStatus.value !== 'all') {
      if (filterStatus.value === 'unlinked') {
        params.unlinked = true
      } else {
        params.status = filterStatus.value
      }
    }
    if (filterType.value && filterType.value !== 'all') params.file_type = filterType.value
    if (filterKbId.value) params.kb_id = filterKbId.value
    const res = await listAllDocuments(params)
    docs.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    message.error(t('documents.loadDocsFailed') + (e?.response?.data?.detail || e.message))
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

function openCreateKb() {
  kbFormMode.value = 'create'
  kbFormId.value = ''
  kbFormName.value = ''
  kbFormDesc.value = ''
  kbFormPrompt.value = ''
  showKbForm.value = true
}

function openRenameKb(kb: KnowledgeBase) {
  kbFormMode.value = 'edit'
  kbFormId.value = kb.id
  kbFormName.value = kb.name
  kbFormDesc.value = kb.description || ''
  kbFormPrompt.value = kb.prompt || ''
  showKbForm.value = true
}

async function handleKbSubmit() {
  if (!kbFormName.value.trim()) return
  kbFormSaving.value = true
  try {
    if (kbFormMode.value === 'create') {
      const res = await createKnowledgeBase({
        name: kbFormName.value.trim(),
        description: kbFormDesc.value.trim() || undefined,
        prompt: kbFormPrompt.value.trim() || undefined,
      })
      const newId = res.data.id
      message.success(t('documents.kbCreated'))
      showKbForm.value = false
      await loadKBs()
      filterKbId.value = newId
      page.value = 1
      router.replace({ query: { ...route.query, kb: newId } })
      await loadDocs()
      return
    } else {
      await updateKnowledgeBase(kbFormId.value, {
        name: kbFormName.value,
        description: kbFormDesc.value || undefined,
        prompt: kbFormPrompt.value || undefined,
      })
      message.success(t('documents.kbUpdated'))
    }
    await loadKBs()
    showKbForm.value = false
  } catch (e: any) {
    message.error((kbFormMode.value === 'create' ? t('documents.kbCreateFailed') : t('documents.kbUpdateFailed')) + (e?.response?.data?.detail || e.message))
  } finally {
    kbFormSaving.value = false
  }
}

async function handleDeleteKb(id: string) {
  try {
    await deleteKnowledgeBase(id)
    if (filterKbId.value === id) {
      selectKb(null)
    }
    await loadKBs()
    message.success(t('documents.kbDeleted'))
  } catch (e: any) {
    message.error(t('documents.kbDeleteFailed') + (e?.response?.data?.detail || e.message))
  }
}

const unaddedUsers = computed(() =>
  allUsers.value.filter((u: any) => !shareUsers.value.some((s: any) => s.id === u.id))
)

const paginatedShareUsers = computed(() => {
  const start = (shareAddedPage.value - 1) * shareAddedPageSize.value
  return shareUsers.value.slice(start, start + shareAddedPageSize.value)
})

const paginatedUnaddedUsers = computed(() => {
  const start = (shareUserPage.value - 1) * shareUserPageSize.value
  return unaddedUsers.value.slice(start, start + shareUserPageSize.value)
})

async function openShare(kbId: string) {
  shareKbId.value = kbId
  shareLoading.value = true
  showShare.value = true
  showAddMoreUsers.value = false
  shareUserSearch.value = ''
  shareUserPage.value = 1
  shareAddedPage.value = 1
  try {
    const r = await client.get(`/kb/${kbId}/users`)
    shareUsers.value = r.data
  } catch { shareUsers.value = [] }
  try {
    // /users now uses server-side pagination, returning {items,total,page,size}; the shared modal searches by username to narrow the scope and takes the first 200 for local filtering/pagination
    const r = await client.get('/users', { params: { size: 200 } })
    allUsers.value = r.data.items
  } catch { allUsers.value = [] }
  shareLoading.value = false
}

async function searchShareUsers() {
  shareLoading.value = true
  try {
    const params: any = { size: 200 }
    if (shareUserSearch.value.trim()) params.search = shareUserSearch.value.trim()
    const r = await client.get('/users', { params })
    allUsers.value = r.data.items
    shareUserPage.value = 1
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
    shareAddedPage.value = 1
    shareUserPage.value = 1
    message.success(t('documents.shareUserAdded'))
  } catch (e: any) {
    message.error(t('documents.addShareUserFailed') + (e?.response?.data?.detail || e.message))
  }
}

async function removeKbUser(uid: string) {
  try {
    await client.delete(`/kb/${shareKbId.value}/users/${uid}`)
    const r = await client.get(`/kb/${shareKbId.value}/users`)
    shareUsers.value = r.data
    shareAddedPage.value = 1
    message.success(t('documents.shareUserRemoved'))
  } catch (e: any) {
    message.error(t('documents.removeShareUserFailed') + (e?.response?.data?.detail || e.message))
  }
}

async function openSelectDocs(kbId: string) {
  showSelectDocs.value = true
  selectedDocIds.value = []
  availablePage.value = 1
  availableSearch.value = ''
  availableStatus.value = ''
  availableType.value = 'all'
  await loadAvailableDocs(kbId)
}

async function loadAvailableDocs(kbId?: string) {
  loadingAvailableDocs.value = true
  try {
    const params: any = { page: availablePage.value, size: availablePageSize.value }
    if (availableSearch.value) params.search = availableSearch.value
    if (availableStatus.value) params.status = availableStatus.value
    if (availableType.value && availableType.value !== 'all') params.file_type = availableType.value
    const res = await listAllDocuments(params)
    availableDocs.value = res.data.items
    availableTotal.value = res.data.total
  } catch (e: any) {
    message.error(t('documents.loadDocsFailed') + (e?.response?.data?.detail || e.message))
  } finally {
    loadingAvailableDocs.value = false
  }
}
function resetAvailableFilters() {
  availableSearch.value = ''
  availableStatus.value = ''
  availableType.value = 'all'
  availablePage.value = 1
  loadAvailableDocs()
}
function onAvailableSearch() {
  availablePage.value = 1
  loadAvailableDocs()
}
function onAvailableStatusChange() {
  availablePage.value = 1
  loadAvailableDocs()
}
function onAvailableTypeChange() {
  availablePage.value = 1
  loadAvailableDocs()
}
function openUploadFromSelectDocs() {
  showSelectDocs.value = false
  uploadTargetKb.value = filterKbId.value
  showUploadModal.value = true
}
function onAvailablePageChange(p: number) {
  availablePage.value = p
  loadAvailableDocs()
}

const allAvailableSelected = computed(() =>
  availableDocs.value.length > 0 && availableDocs.value.every(d => selectedDocIds.value.includes(d.id))
)
const someAvailableSelected = computed(() =>
  availableDocs.value.some(d => selectedDocIds.value.includes(d.id))
)
function toggleSelectAllAvailable() {
  if (allAvailableSelected.value) {
    const visible = new Set(availableDocs.value.map(d => d.id))
    selectedDocIds.value = selectedDocIds.value.filter(id => !visible.has(id))
  } else {
    const merged = new Set([...selectedDocIds.value, ...availableDocs.value.map(d => d.id)])
    selectedDocIds.value = [...merged]
  }
}

async function handleSelectDocs() {
  if (selectedDocIds.value.length === 0 || !filterKbId.value) return
  linkingDocs.value = true
  try {
    const res = await addDocumentsToKB(filterKbId.value, selectedDocIds.value)
    message.success(
      t('documents.docsAdded', { added: res.data.added }) +
      (res.data.skipped > 0 ? t('documents.docsSkipped', { skipped: res.data.skipped }) : '')
    )
    showSelectDocs.value = false
    await loadKBs()
    await loadDocs()
  } catch (e: any) {
    message.error(t('documents.linkDocsFailed') + (e?.response?.data?.detail || e.message))
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
function resetFilters() {
  search.value = ''
  filterStatus.value = 'all'
  filterType.value = 'all'
  page.value = 1
  loadDocs()
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
      message.warning(t('documents.fileTooLarge', { name: f.name, size: (f.size / 1024 / 1024).toFixed(1) }))
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
  message.success(t('documents.uploadComplete'))
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
    message.success(t('documents.docDeleted'))
  } catch (e: any) {
    message.error(t('documents.docDeleteFailed') + (e?.response?.data?.detail || e.message))
  }
}

async function handleUnlink(doc: DocumentItem) {
  if (!filterKbId.value) return
  const kbId = filterKbId.value
  try {
    await removeDocumentFromKB(kbId, doc.id)
    message.success(t('documents.unlinkedFromKb', { kb: filterKbName.value }))
    docs.value = docs.value.filter(d => d.id !== doc.id)
    total.value -= 1
    if (detailDoc.value && detailDoc.value.id === doc.id) {
      detailDoc.value = { ...detailDoc.value, kb_ids: detailDoc.value.kb_ids.filter(id => id !== kbId) }
    }
  } catch (e: any) {
    message.error(t('documents.unlinkFailed') + (e?.response?.data?.detail || e.message))
  }
}

async function handleUnlinkDocKb(kbId: string) {
  if (!detailDoc.value) return
  const docId = detailDoc.value.id
  const kb = allKbs.value.find(k => k.id === kbId)
  try {
    await removeDocumentFromKB(kbId, docId)
    message.success(t('documents.unlinkedFromKb', { kb: kb?.name || t('documents.knowledgeBase') }))
    detailDoc.value = { ...detailDoc.value, kb_ids: detailDoc.value.kb_ids.filter(id => id !== kbId) }
    selectedDocKbIds.value = selectedDocKbIds.value.filter(id => id !== kbId)
    if (filterKbId.value === kbId) {
      docs.value = docs.value.filter(d => d.id !== docId)
      total.value -= 1
    }
  } catch (e: any) {
    message.error(t('documents.unlinkFailed') + (e?.response?.data?.detail || e.message))
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
    message.error(t('documents.downloadFailed') + (e?.message || t('documents.unknownError')))
  }
}

// Loads the current page of chunks from the backend (server-side pagination + search)
async function loadChunks() {
  if (!chunkDocId.value) return
  chunksLoading.value = true
  try {
    const res = await getDocumentChunks(chunkDocId.value, {
      page: chunkPage.value,
      size: chunksPerPage,
      search: chunkSearch.value.trim() || undefined,
    })
    chunks.value = res.data.items
    chunkTotal.value = res.data.total
    // Expand all chunks by default so the content is fully shown (not folded)
    expandedChunks.value = new Set(res.data.items.map(c => c.id))
  } catch {
    message.error(t('documents.loadChunksFailed'))
  } finally {
    chunksLoading.value = false
  }
}

async function openChunks(docId: string) {
  chunkDocId.value = docId
  chunkSearch.value = ''
  chunkPage.value = 1
  expandedChunks.value = new Set()
  showDetailChunks.value = true
  await loadChunks()
  await nextTick()
  chunkPreviewTitle.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// Triggered on search input change: reset to page 1 and reload
function onChunkSearch(value: string) {
  chunkSearch.value = value
  chunkPage.value = 1
  loadChunks()
}

// Triggered on pagination change: load the selected page
function onChunkPageChange(page: number) {
  chunkPage.value = page
  loadChunks()
}

// ── Helpers ──

const statusColors: Record<string, string> = {
  pending: 'default', uploaded: 'default',
  parsing: 'warning', chunking: 'warning',
  embedding: 'info', chunked: 'warning', completed: 'success', failed: 'error',
}
const statusLabels: Record<string, string> = {
  pending: t('documents.status.waiting'), uploaded: t('documents.status.uploaded'),
  parsing: t('documents.status.parsing'), chunking: t('documents.status.chunking'),
  embedding: t('documents.status.embedding'), chunked: t('documents.status.chunked'),
  completed: t('documents.status.completed'), failed: t('documents.status.failed'),
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
  const opts: { label: string; value: string }[] = [{ label: t('documents.allTypes'), value: 'all' }]
  for (const ext of supportedExts.value) {
    const label = getFileTypeConfig(ext).label
    // Avoid duplicate labels for multi-ext parsers (e.g. md + markdown)
    if (!opts.some(o => o.label === label)) {
      opts.push({ label, value: ext })
    }
  }
  return opts
})

// Build the upload-zone hint text from the live supported-extensions list,
// so disabling a plugin via /admin/plugins immediately reflects here.
const supportedFormatsHint = computed(() => {
  if (supportedExts.value.length === 0) return t('documents.loadingFormats')
  const labels = Array.from(new Set(
    supportedExts.value.map(ext => getFileTypeConfig(ext).label)
  ))
  return t('documents.supportedFormats', { formats: labels.join('、') })
})

const statusOptions = [
  { label: t('documents.allStatus'), value: 'all' },
  { label: t('documents.status.completed'), value: 'completed' }, { label: t('documents.status.processing'), value: 'pending' },
  { label: t('documents.status.waiting'), value: 'pending' }, { label: t('documents.status.failed'), value: 'failed' },
  { label: t('documents.status.chunked'), value: 'chunked' },
  { label: t('documents.unlinked'), value: 'unlinked' },
]

const availableStatusOptions = [
  { label: t('documents.allStatus'), value: '' },
  { label: t('documents.status.completed'), value: 'completed' },
  { label: t('documents.status.processing'), value: 'pending' },
  { label: t('documents.status.chunked'), value: 'chunked' },
  { label: t('documents.status.failed'), value: 'failed' },
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
    <PageHeader :title="t('nav.documents')" :icon="DocumentText">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NButton size="small" type="primary" @click="openCreateKb">
          <template #icon><NIcon><Create /></NIcon></template>
          {{ t('documents.newKb') }}
        </NButton>
        <NButton size="small" type="primary" @click="openUploadModal">
          <template #icon><NIcon><Add /></NIcon></template>
          {{ t('documents.uploadDoc') }}
        </NButton>
      </template>
    </PageHeader>

    <!-- KB Form Modal (create + edit share the same modal) -->
    <AppModal v-model:show="showKbForm"
      :title="kbFormMode === 'create' ? t('documents.newKb') : t('documents.editKb')"
      size="nested"
    >
      <div class="kb-form">
        <NInput v-model:value="kbFormName" :placeholder="t('documents.kbNamePlaceholder')" />
        <NInput v-model:value="kbFormDesc" :placeholder="t('documents.descOptional')" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
        <NInput v-model:value="kbFormPrompt" :placeholder="t('documents.promptHint')" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showKbForm = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" :loading="kbFormSaving" :disabled="!kbFormName.trim()" @click="handleKbSubmit">
            {{ kbFormMode === 'create' ? t('documents.create') : t('common.save') }}
          </NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Upload Modal -->
    <AppModal v-model:show="showUploadModal" :title="t('documents.uploadFile')" size="detail">
      <div class="upload-modal-body">
        <!-- Knowledge base selector -->
        <div class="upload-kb-select">
          <span class="upload-kb-label">{{ t('documents.linkKb') }}</span>
          <span class="upload-kb-value">{{ uploadTargetKbName }}</span>
          <NButton size="small" @click="openKbFilter('upload')">{{ t('common.switch') }}</NButton>
        </div>

        <!-- Drop zone -->
        <div :class="['upload-zone', { dragover: dragOver }]"
          @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop"
          @click="triggerFileSelect"
        >
          <div class="upload-zone-content">
            <NIcon size="36" color="var(--color-primary)"><CloudUpload /></NIcon>
            <p>{{ t('documents.dragDropHint') }}</p>
            <span class="upload-hint">{{ supportedFormatsHint }}</span>
          </div>
        </div>

        <!-- Per-file queue -->
        <div v-if="uploadItems.length > 0" class="upload-queue">
          <div class="upload-queue-header">
            <span>{{ t('documents.fileCount', { count: uploadItems.length }) }}</span>
            <NButton size="small" @click="clearUploadItems" :disabled="hasActiveUploads">{{ t('documents.clearCompleted') }}</NButton>
          </div>
          <div v-for="item in uploadItems" :key="item.id" class="upload-file-row">
            <div class="upload-file-info">
              <span class="upload-file-name">📄 {{ item.name }}</span>
              <span class="upload-file-size">{{ formatSize(item.size) }}</span>
              <NTag :type="item.status === 'success' ? 'success' : item.status === 'error' ? 'error' : item.status === 'cancelled' ? 'warning' : item.status === 'uploading' ? 'info' : 'default'" size="tiny" :bordered="false">
                {{ item.status === 'pending' ? t('documents.upload.waiting') : item.status === 'uploading' ? t('documents.upload.uploading') : item.status === 'success' ? t('documents.upload.complete') : item.status === 'error' ? t('documents.upload.failed') : t('documents.upload.cancelled') }}
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
          <NButton type="primary" :loading="hasActiveUploads" :disabled="pendingCount === 0" @click="startUploads">
            {{ hasActiveUploads ? t('documents.uploading') : pendingCount > 0 ? t('documents.startUploadCount', { count: pendingCount }) : t('documents.startUpload') }}
          </NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- KB Filter -->
    <div class="dm-kb-filter">
      <span class="dm-kb-label">{{ t('documents.currentKb') }}</span>
      <div class="dm-kb-panel">
        <div class="dm-kb-top">
          <NButton secondary :style="{ fontWeight: 700 }" @click="openKbFilter('filter')">
            <template #icon><NIcon><DocumentText /></NIcon></template>
            {{ filterKbId ? filterKbName : t('common.all') }}
          </NButton>
          <span v-if="filterKbDesc" class="dm-kb-desc">{{ filterKbDesc }}</span>
        </div>
        <div v-if="selectedKb" class="dm-kb-bottom">
          <span class="dm-kb-count">📄 {{ t('documents.kbDocMeta', { count: selectedKb.doc_count }) }}</span>
          <span class="dm-kb-count">🧬 {{ t('documents.kbVectorMeta', { count: selectedKb.vector_count }) }}</span>
          <NSpace class="dm-kb-actions" size="small">
            <NButton size="small" @click="openRenameKb(selectedKb); blurActive()">
              <template #icon><NIcon size="14"><Create /></NIcon></template>
              {{ t('documents.editKb') }}
            </NButton>
            <NButton size="small" @click="goToChat(selectedKb.id); blurActive()">
              <template #icon><NIcon size="14"><Chatbubbles /></NIcon></template>
              {{ t('documents.startChat') }}
            </NButton>
            <NTooltip trigger="hover">
              <template #trigger>
                <NButton v-if="auth.isStaff" size="small" @click="openShare(selectedKb.id); blurActive()">
                  <template #icon><NIcon size="14"><People /></NIcon></template>
                  {{ t('documents.shareUsers') }}
                </NButton>
              </template>
              {{ t('documents.shareUsersTooltip') }}
            </NTooltip>
            <NButton size="small" @click="openSelectDocs(selectedKb.id); blurActive()">
              <template #icon><NIcon size="14"><Search /></NIcon></template>
              {{ t('documents.addDocs') }}
            </NButton>
            <NPopconfirm @positive-click="handleDeleteKb(selectedKb.id)">
              <template #trigger>
                <NTooltip trigger="hover">
                  <template #trigger>
                    <NButton size="small" class="dm-danger-btn" @click="blurActive()" :style="{ '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444', '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626', '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626' }">
                      <template #icon><NIcon size="14"><Trash /></NIcon></template>
                      {{ t('common.delete') }}
                    </NButton>
                  </template>
                  {{ t('documents.deleteKbTooltip') }}
                </NTooltip>
              </template>
              {{ t('documents.confirmDeleteKb', { kb: selectedKb.name }) }}
            </NPopconfirm>
          </NSpace>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" :placeholder="t('common.searchFilename')" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterStatus" :options="statusOptions" :placeholder="t('common.status')" size="small" style="width:120px" @update:value="onSearch" />
      <NSelect v-model:value="filterType" :options="typeOptions" :placeholder="t('common.type')" size="small" style="width:120px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>{{ t('common.reset') }}</NButton>
    </div>

    <!-- Doc List -->
    <NSpin :show="loading">
      <NEmpty v-if="!loading && docs.length === 0" :description="t('documents.noDocsUpload')" />
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
            <NPopconfirm
              v-if="filterKbId && doc.kb_ids.includes(filterKbId)"
              @positive-click="handleUnlink(doc)"
            >
              <template #trigger>
                <NButton size="tiny" quaternary type="error" class="doc-unlink-btn" @click.stop>
                  <template #icon><NIcon><Remove /></NIcon></template>
                </NButton>
              </template>
              {{ t('documents.confirmUnlinkDoc', { kb: filterKbName }) }}
            </NPopconfirm>
          </div>
          <div class="doc-card-meta">
            <span>{{ t('documents.linkedKbs', { count: doc.kb_ids.length }) }}</span>
            <span class="doc-meta-sep">·</span>
            <span>{{ t('documents.chunkCount', { count: doc.chunk_count }) }}</span>
            <span class="doc-meta-sep">·</span>
            <span>{{ formatSize(doc.file_size) }}</span>
            <span class="doc-meta-sep">·</span>
            <span class="doc-meta-muted">{{ formatDate(doc.created_at) }}</span>
            <NTag :type="statusColors[doc.status] as any" size="small" :bordered="false" class="doc-status-tag">
              {{ statusLabels[doc.status] || doc.status }}
            </NTag>
          </div>
          <div v-if="isProcessing(doc.status)" class="doc-card-progress">
            <NProgress
              type="line"
              :percentage="doc.progress"
              :height="6"
              :border-radius="3"
              :color="statusColors[doc.status] === 'warning' ? '#f59e0b' : '#3b82f6'"
              :rail-color="'var(--color-border)'"
              style="flex:1; min-width:100px"
            />
            <span class="doc-progress-text">{{ doc.progress }}%</span>
          </div>
        </NCard>
      </div>
    </NSpin>

    <AppPagination :page="page" :page-size="size" :item-count="total" @update:page="onPageChange" />

    <!-- Doc KBs Modal -->
    <AppModal v-model:show="showDocKbs" :title="t('documents.linkKb')"
      size="nested"
      @after-leave="docKbSearchText = ''"
    >
      <NInput
        v-if="allKbs.length > 0"
        v-model:value="docKbSearchText"
        :placeholder="t('documents.searchKbName')"
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
            <div class="kb-pick-row">
              <div class="kb-pick-info">
                <strong>{{ kb.name }}</strong>
                <span v-if="kb.description" class="kb-pick-desc">{{ kb.description }}</span>
                <span class="kb-pick-meta">{{ t('documents.countMeta', { docs: kb.doc_count, vectors: kb.vector_count }) }}</span>
              </div>
              <NPopconfirm @positive-click="handleUnlinkDocKb(kb.id)">
                <template #trigger>
                  <NButton size="tiny" type="error" class="kb-pick-unlink" @click.stop>
                    <template #icon><NIcon><Remove /></NIcon></template>
                  </NButton>
                </template>
                {{ t('documents.confirmUnlinkDoc', { kb: kb.name }) }}
              </NPopconfirm>
            </div>
          </NCard>
        </div>
      </template>
      <NEmpty v-else :description="t('documents.noMatchingKb')" style="padding:16px 0" />
    </AppModal>

    <!-- Document Detail Modal -->
    <AppModal v-model:show="showDetail" :title="detailDoc?.filename || t('documents.docDetail')"
      size="detail"
      @after-leave="detailDoc = null; showDetailChunks = false"
    >
      <div v-if="detailDoc">
        <NDescriptions bordered :column="1" size="small" label-placement="left" label-style="width: 120px">
          <NDescriptionsItem :label="t('documents.fileName')">{{ detailDoc.filename }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('documents.fileType')">
            {{ getFileTypeConfig(detailDoc.file_type).label }} ({{ detailDoc.file_type }})
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('documents.fileSize')">{{ formatSize(detailDoc.file_size) }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('common.status')">
            <NTag :type="statusColors[detailDoc.status] as any" size="small">
              {{ statusLabels[detailDoc.status] || detailDoc.status }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem v-if="detailDoc.status === 'failed' && detailDoc.error_message" :label="t('documents.errorMessage')">
            {{ detailDoc.error_message }}
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('documents.chunkNumber')">
            <span
              v-if="detailDoc.chunk_count > 0"
              class="doc-kb-link"
              @click="openChunks(detailDoc.id)"
              role="button"
              tabindex="0"
              @keydown.enter.prevent="openChunks(detailDoc.id)"
              @keydown.space.prevent="openChunks(detailDoc.id)"
            >{{ t('documents.chunkCount', { count: detailDoc.chunk_count }) }}</span>
            <span v-else>0</span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('documents.linkedKbsLabel')">
            <span
              :class="detailDoc.kb_ids.length > 0 ? 'doc-kb-link' : 'doc-meta-muted'"
              @click="openDocKbs(detailDoc.kb_ids)"
              role="button"
              tabindex="0"
              @keydown.enter.prevent="openDocKbs(detailDoc.kb_ids)"
              @keydown.space.prevent="openDocKbs(detailDoc.kb_ids)"
            >
              {{ detailDoc.kb_ids.length > 0 ? t('documents.linkedKbs', { count: detailDoc.kb_ids.length }) : t('documents.notLinkedKb') }}
            </span>
          </NDescriptionsItem>
          <NDescriptionsItem :label="t('common.createdAt')">{{ formatDateTime(detailDoc.created_at) }}</NDescriptionsItem>
          <NDescriptionsItem v-if="detailDoc.updated_at" :label="t('common.updatedAt')">{{ formatDateTime(detailDoc.updated_at) }}</NDescriptionsItem>
          <NDescriptionsItem :label="t('documents.docId')">{{ detailDoc.id }}</NDescriptionsItem>
        </NDescriptions>

        <!-- Chunk preview: revealed inline when the chunk count link is clicked -->
        <div v-if="showDetailChunks" class="detail-chunks">
          <h3 ref="chunkPreviewTitle" class="chunk-preview-title">{{ t('documents.chunkPreview') }}</h3>
          <div class="chunks-modal">
            <NInput
              v-if="chunkTotal > 0 || chunkSearch"
              v-model:value="chunkSearch"
              :placeholder="t('documents.searchChunkContent')"
              size="small"
              clearable
              @update:value="onChunkSearch"
              style="margin-bottom:12px"
            >
              <template #prefix><NIcon size="15"><Search /></NIcon></template>
            </NInput>

            <NSpin :show="chunksLoading">
              <NEmpty
                v-if="!chunksLoading && chunks.length === 0"
                :description="chunkSearch ? t('documents.noMatchingChunks') : t('documents.noChunkData')"
              />

              <div v-if="chunks.length > 0">
                <div class="chunk-count">{{ t('documents.chunkTotal', { count: chunkTotal }) }}</div>
                <NCard v-for="c in chunks" :key="c.id" size="small" class="chunk-card">
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
                    {{ expandedChunks.has(c.id) ? t('common.collapse') : t('common.expand') }}
                  </NButton>
                </NCard>
              </div>
            </NSpin>
          </div>
          <AppPagination
            v-if="totalChunkPages > 1"
            class="chunk-footer-pager"
            :page="chunkPage"
            :page-size="chunksPerPage"
            :item-count="chunkTotal"
            @update:page="onChunkPageChange"
          />
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton v-if="detailDoc" @click="handleDownload(detailDoc)">{{ t('documents.downloadOriginal') }}</NButton>
          <NPopconfirm v-if="detailDoc" @positive-click="deleteDetailDoc">
            <template #trigger>
              <NButton type="error">{{ t('common.delete') }}</NButton>
            </template>
            {{ t('documents.confirmDeleteDoc', { filename: detailDoc.filename }) }}
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>

    <!-- KB Filter Modal (reuses the shared component) -->
    <KbPickerModal
      v-model:show="showKbFilter"
      :kbs="allKbs"
      :selected-id="kbFilterMode === 'upload' ? uploadTargetKb : filterKbId"
      :show-all="true"
      :all-label="kbFilterMode === 'upload' ? t('documents.notLinked') : t('common.all')"
      :all-meta="kbFilterMode === 'upload' ? t('documents.dontUploadToKb') : t('documents.showAllDocs')"
      :all-active="kbFilterMode === 'upload' ? uploadTargetKb === null : filterKbId === null"
      :all-count="allKbs.length"
      :sortable="true"
      :page-size="12"
      @select="onKbFilterSelect"
    />

    <!-- Share Modal -->
    <AppModal v-model:show="showShare" :title="t('documents.shareUsers')" size="detail">
      <div class="share-form">
        <NSpin :show="shareLoading">
          <div v-if="!shareLoading && shareUsers.length === 0" class="share-empty">
            <NEmpty :description="t('documents.noSharedUsers')" />
          </div>
          <div v-if="shareUsers.length > 0">
            <div class="share-list">
              <div v-for="u in paginatedShareUsers" :key="u.id" class="share-card">
                <div class="share-card-header">
                  <div class="share-user-info-row">
                    <span class="share-user-avatar">👤</span>
                    <div class="share-user-title">
                      <div class="share-user-name">{{ u.display_name || u.username }}</div>
                      <div class="share-user-sub">{{ u.username }} · {{ u.role === 'admin' ? t('common.role.adminShort') : t('common.role.regular') }}</div>
                    </div>
                  </div>
                  <NPopconfirm @positive-click="removeKbUser(u.id)">
                    <template #trigger>
                      <NButton
                        class="share-card-remove"
                        size="tiny"
                        text
                        type="error"
                        @click.stop
                      >
                        <template #icon><NIcon size="16"><Close /></NIcon></template>
                      </NButton>
                    </template>
                    {{ t('documents.confirmUnshareUser') }}
                  </NPopconfirm>
                </div>
              </div>
            </div>
            <AppPagination
              :page="shareAddedPage"
              :page-size="shareAddedPageSize"
              :item-count="shareUsers.length"
              @update:page="shareAddedPage = $event"
            />
          </div>
          <div class="share-add-more">
            <NButton dashed block class="doc-unlink-btn share-add-more-btn" @click="showAddMoreUsers = !showAddMoreUsers; if (showAddMoreUsers) searchShareUsers()">
              <template #icon><NIcon><component :is="showAddMoreUsers ? Remove : Add" /></NIcon></template>
              {{ t('documents.addMoreUsers') }}
            </NButton>
          </div>
          <template v-if="showAddMoreUsers">
            <div class="share-add-row">
              <NInput v-model:value="shareUserSearch" :placeholder="t('documents.searchUser')" clearable @keyup.enter="searchShareUsers" style="flex:1">
                <template #prefix><NIcon><Search /></NIcon></template>
              </NInput>
              <NButton type="primary" @click="searchShareUsers">
                <template #icon><NIcon><Search /></NIcon></template>
                {{ t('common.search') }}
              </NButton>
            </div>
            <div v-if="unaddedUsers.length === 0" class="share-empty">
              <NEmpty :description="t('documents.noUsersToAdd')" />
            </div>
            <div v-if="unaddedUsers.length > 0">
              <div class="share-list share-unadded-list">
                <div
                  v-for="u in paginatedUnaddedUsers"
                  :key="u.id"
                  class="share-card share-card-addable"
                  role="button"
                  tabindex="0"
                  @click="addKbUser(u.id)"
                  @keydown.enter.prevent="addKbUser(u.id)"
                  @keydown.space.prevent="addKbUser(u.id)"
                >
                  <div class="share-card-header">
                    <div class="share-user-info-row">
                      <span class="share-user-avatar">👤</span>
                      <div class="share-user-title">
                        <div class="share-user-name">{{ u.display_name || u.username }}</div>
                        <div class="share-user-sub">{{ u.username }} · {{ u.role === 'admin' ? t('common.role.adminShort') : t('common.role.regular') }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <AppPagination
                :page="shareUserPage"
                :page-size="shareUserPageSize"
                :item-count="unaddedUsers.length"
                @update:page="shareUserPage = $event"
              />
            </div>
          </template>
        </NSpin>
      </div>
    </AppModal>

    <!-- Select Documents Modal -->
    <AppModal v-model:show="showSelectDocs" :title="t('documents.selectDocsToAdd')" size="wide">
      <div class="select-docs-modal">
        <div class="select-docs-filters">
          <NInput v-model:value="availableSearch" :placeholder="t('common.searchFilename')" clearable @keyup.enter="onAvailableSearch" style="flex:1">
            <template #prefix><NIcon><Search /></NIcon></template>
          </NInput>
          <NButton type="primary" @click="onAvailableSearch">
            <template #icon><NIcon><Search /></NIcon></template>
            {{ t('common.search') }}
          </NButton>
          <NSelect v-model:value="availableStatus" :options="availableStatusOptions" :placeholder="t('common.status')" style="width:110px" @update:value="onAvailableStatusChange" />
          <NSelect v-model:value="availableType" :options="typeOptions" :placeholder="t('common.type')" style="width:110px" @update:value="onAvailableTypeChange" />
          <NButton @click="resetAvailableFilters" secondary>{{ t('common.reset') }}</NButton>
        </div>
        <NSpin :show="loadingAvailableDocs">
          <div v-if="!loadingAvailableDocs && availableDocs.length === 0" class="select-docs-empty">
            <NEmpty :description="t('documents.noAvailableDocs')" />
            <NButton type="primary" dashed @click="showSelectDocs = false; router.push('/documents')">
              <template #icon><NIcon><Add /></NIcon></template>
              {{ t('documents.goToUploadDocs') }}
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
              <span class="select-doc-name" :title="doc.filename">{{ doc.filename }}</span>
            </div>
          </div>
          <AppPagination
            :page="availablePage"
            :page-size="availablePageSize"
            :item-count="availableTotal"
            @update:page="onAvailablePageChange"
          />
        </NSpin>
        <div class="select-docs-actions">
          <div class="select-docs-left">
            <NCheckbox
              :checked="allAvailableSelected"
              :indeterminate="someAvailableSelected && !allAvailableSelected"
              @update:checked="toggleSelectAllAvailable"
            >{{ t('documents.selectAll') }}</NCheckbox>
            <span class="select-docs-count">{{ t('documents.selectedPrefix') }}{{ selectedDocIds.length }}{{ availableTotal ? t('documents.totalDocsSuffix', { count: availableTotal }) : '' }}</span>
            <NButton text size="tiny" type="primary" @click="openUploadFromSelectDocs">
              {{ t('documents.uploadMoreDocs') }} →
            </NButton>
          </div>
          <NSpace>
            <NButton @click="showSelectDocs = false">{{ t('common.cancel') }}</NButton>
            <NButton type="primary" :disabled="selectedDocIds.length === 0" :loading="linkingDocs" @click="handleSelectDocs">
              {{ t('documents.addToKb') }}
            </NButton>
          </NSpace>
        </div>
      </div>
    </AppModal>
  </div>
</template>
<style scoped>
.dm-view { height: 100%; overflow-y: auto; }

/* Create KB Modal */


/* Upload Modal */
.upload-kb-select { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.upload-kb-label { font-size: var(--text-sm); font-weight: 500; white-space: nowrap; }
.upload-kb-value { flex: 0 1 auto; font-size: var(--text-sm); font-weight: 600; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.upload-modal-body { display: flex; flex-direction: column; gap: 12px; max-height: 50vh; overflow-y: auto; }
.upload-zone { border: 2px dashed var(--color-border); border-radius: 8px; padding: 28px; text-align: center; cursor: pointer; transition: all .2s; }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--color-primary); background: rgba(59,130,246,0.04); }
.upload-zone-content p { margin: 10px 0 4px; font-weight: 500; }
.upload-hint { font-size: 0.8rem; color: var(--color-text-muted); }

.upload-queue { display: flex; flex-direction: column; gap: 8px; }
.upload-queue-header { display: flex; justify-content: space-between; align-items: center; font-weight: 500; font-size: var(--text-sm); }
.upload-file-row { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius); box-shadow: var(--shadow-sm); transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease; }
.upload-file-row:hover { border-color: var(--color-primary); box-shadow: var(--shadow); transform: translateY(-1px); }
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
.dm-card {
  cursor: pointer;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border-color: var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.dm-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.dm-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}

.doc-card-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
}
.doc-card-icon { font-size: 1.6rem; }
.doc-unlink-btn {
  flex-shrink: 0;
  margin-left: auto;
  opacity: 0;
  transition: opacity .15s;
  padding: 0 4px;
  height: 22px;
  --n-color: transparent;
  --n-color-hover: transparent;
  --n-color-pressed: transparent;
  --n-color-focus: transparent;
}
.doc-unlink-btn :deep(.n-icon) {
  font-size: 13px;
  color: #dc2626;
}
:global(html.dark) .doc-unlink-btn :deep(.n-icon) {
  color: #fca5a5;
}
/* Add/collapse user button: reuses the borderless style but uses the primary color (not red, not a destructive action) */
.share-add-more-btn {
  opacity: 1;
  margin-left: 0;
  width: 100%;
  justify-content: center;
  --n-text-color: #3b82f6;
  --n-text-color-hover: #2563eb;
  --n-text-color-pressed: #2563eb;
  --n-icon-color: #3b82f6;
}
.share-add-more-btn :deep(.n-icon) {
  font-size: 13px;
  color: #3b82f6;
}
:global(html.dark) .share-add-more-btn,
:global(html.dark) .share-add-more-btn :deep(.n-icon) {
  --n-text-color: #60a5fa;
  --n-text-color-hover: #93c5fd;
  --n-icon-color: #60a5fa;
  color: #60a5fa;
}
:global(html.dark) .doc-unlink-btn :deep(.n-icon) {
  color: #f87171;
}
/* Dim primary buttons slightly in dark mode (header actions + modals) — they read too bright on the dark surface */
:global(html.dark) .dm-view :deep(.n-button.n-button--primary-type) {
  filter: brightness(0.85);
}
.dm-card:hover .doc-unlink-btn { opacity: 1; }
.doc-card-title-wrap {
  flex: 1;
  min-width: 0;
}
.doc-card-title-wrap .doc-name {
  max-width: 100%;
  display: block;
}
.doc-status-tag { flex-shrink: 0; align-self: center; }
.doc-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-wrap: wrap;
  line-height: 1.5;
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
  font-weight: 500;
  font-size: 0.8125rem;
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

/* Chunks */
/* Panel owns its height; the inner list scrolls while the pager stays pinned at the bottom */
.detail-chunks {
  display: flex;
  flex-direction: column;
  max-height: 80vh;
  margin-top: var(--space-6, 24px);
  padding-top: var(--space-4, 16px);
  border-top: 1px solid var(--color-border, #eee);
}
.chunk-preview-title {
  flex-shrink: 0;
  margin: 0 0 var(--space-3, 12px);
  font-size: var(--text-base, 15px);
  font-weight: 600;
  color: var(--color-text, #1f2937);
}
/* Inner scroll region: flex:1 + min-height:0 is what makes scrolling work inside a flex column */
.chunks-modal {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
/* Pager = plain flex footer of the panel: stays at the panel bottom while the
   inner list scrolls, but scrolls away with the panel when the outer modal scrolls */
.detail-chunks > .chunk-footer-pager {
  flex-shrink: 0;
  margin-top: 8px;
  padding-top: 8px;
  background: var(--modal-content-bg, #fff);
  border-top: 1px solid var(--color-border, #eee);
}
.chunk-count { font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 10px; }
.chunk-card {
  margin-bottom: 8px;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border-color: var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.chunk-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
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
.kb-pick-row { display: flex; align-items: flex-start; gap: 10px; }
.kb-pick-info { flex: 1; min-width: 0; }
.kb-pick-unlink { flex-shrink: 0; margin-top: 2px; opacity: 0; transition: opacity .15s; padding: 0 4px; height: 22px; }
.kb-pick-unlink :deep(.n-icon) { font-size: 13px; }
.kb-pick-card:hover .kb-pick-unlink { opacity: 1; }
.kb-pick-desc { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-meta { font-size: 0.65rem; color: var(--color-text-muted); }

/* KB action modals */
.kb-form { display: flex; flex-direction: column; gap: 12px; }
.share-form { display: flex; flex-direction: column; max-height: 60vh; }
.share-modal-hint { margin: 0 0 12px; font-size: var(--text-sm); color: var(--color-text-muted); }
.share-add-row { display: flex; gap: 8px; margin-bottom: 16px; }
.share-empty { padding: 20px 0; }
.share-list { flex: 1; overflow-y: auto; min-height: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.share-card {
  position: relative;
  padding: 12px;
  border: 1px solid var(--color-card-border);
  border-radius: var(--radius);
  background: var(--color-card-bg);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.share-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.share-card:hover .share-card-remove { opacity: 1; }
.share-card-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.share-card-remove { opacity: 0; transition: opacity .2s; flex-shrink: 0; }
.share-card-addable { cursor: pointer; }
.share-card-addable:hover { border-color: var(--color-primary); background: rgba(59, 130, 246, 0.04); }
.share-card-addable:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -1px; border-radius: var(--radius); }
.share-add-more { margin-top: 12px; }
.share-user-info-row { display: flex; align-items: center; gap: 10px; min-width: 0; }
.share-user-title { flex: 1; min-width: 0; }
.share-user-avatar { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--color-border); border-radius: 50%; font-size: 0.9rem; flex-shrink: 0; }
.share-user-name { font-weight: 500; font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-user-sub { font-size: 0.75rem; color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.select-docs-modal { display: flex; flex-direction: column; gap: 12px; max-height: 70vh; }
.select-docs-filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.select-docs-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-top: 4px;
  max-height: 55vh;
  overflow-y: auto;
  overflow-x: hidden;
}
.select-doc-row { display: flex; align-items: center; gap: 10px; padding: 10px 8px; cursor: pointer; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius); box-shadow: var(--shadow-sm); transition: background .15s, border-color .15s ease, box-shadow .15s ease, transform .15s ease; min-width: 0; }
.select-doc-row:hover { background: rgba(59, 130, 246, 0.04); border-color: var(--color-primary); box-shadow: var(--shadow); transform: translateY(-1px); }
.select-doc-row.selected { background: rgba(59, 130, 246, 0.1); border-color: var(--color-primary); box-shadow: var(--shadow); }
.select-doc-row:focus-visible { outline: none; border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-primary-soft); }
.select-doc-name { font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.select-docs-actions { display: flex; justify-content: space-between; align-items: center; padding-top: 8px; border-top: 1px solid var(--color-border); }
.select-docs-left { display: flex; align-items: center; gap: 12px; }
.select-docs-empty { display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 24px 0; }
.select-docs-count { font-size: var(--text-sm); color: var(--color-text-muted); }

@media (max-width: 640px) {
  .share-list { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 420px) {
  .share-list { grid-template-columns: 1fr; }
}
</style>