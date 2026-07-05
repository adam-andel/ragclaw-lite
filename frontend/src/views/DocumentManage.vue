<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NSpace, NSpin, NEmpty, NProgress,
  NInput, NSelect, NPagination, NPopconfirm, useMessage,
  NIcon, NModal, NCard, NDescriptions, NDescriptionsItem,
} from 'naive-ui'
import { CloudUpload, Search, DocumentText, Add } from '@vicons/ionicons5'
import {
  uploadDocument, uploadDocumentsBatch, listAllDocuments,
  getDocumentStatus, getDocumentChunks, deleteDocument,
  listKnowledgeBases, getSupportedTypes, downloadDocument,
} from '@/api/documents'
import { useAuthStore } from '@/stores/auth'
import type { DocumentItem, ChunkItem } from '@/types'

const message = useMessage()
const auth = useAuthStore()
const router = useRouter()

// List state
const docs = ref<DocumentItem[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const loading = ref(false)
const search = ref('')
const filterStatus = ref<string | null>(null)
const filterType = ref<string | null>(null)

// Supported extensions — loaded from /documents/supported-types at mount time
const supportedExts = ref<string[]>([])

// Upload modal
const showUploadModal = ref(false)

// Upload state
const uploading = ref(false)
const uploadFiles = ref<File[]>([])
const uploadProgress = ref(0)
const dragOver = ref(false)

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

onMounted(() => { loadDocs(); startPolling(); loadKBs(); loadSupportedTypes() })

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function loadDocs() {
  loading.value = true
  try {
    const params: any = { page: page.value, size: size.value }
    if (search.value) params.search = search.value
    if (filterStatus.value) params.status = filterStatus.value
    if (filterType.value) params.file_type = filterType.value
    const res = await listAllDocuments(params)
    docs.value = res.data.items
    total.value = res.data.total
  } catch (e: any) {
    message.error('加载文档失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loading.value = false
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
      message.warning(`文件过大：${f.name} (${(f.size/1024/1024).toFixed(1)}MB)`)
      continue
    }
    uploadFiles.value.push(f)
  }
}

function removeFile(index: number) {
  uploadFiles.value.splice(index, 1)
}

function openUploadModal() {
  showUploadModal.value = true
}

async function handleUpload() {
  if (uploadFiles.value.length === 0) return
  uploading.value = true
  try {
    if (uploadFiles.value.length === 1) {
      await uploadDocument(uploadFiles.value[0], (pct) => uploadProgress.value = pct)
    } else {
      await uploadDocumentsBatch(uploadFiles.value, (pct) => uploadProgress.value = pct)
    }
    message.success('上传成功，正在后台处理…')
    uploadFiles.value = []
    uploadProgress.value = 0
    await loadDocs()
  } catch (e: any) {
    message.error('上传失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
  }
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
]

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

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
    <div class="dm-header">
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><DocumentText /></NIcon>
        <h2>文档管理</h2>
        <span v-if="total > 0" class="kb-header-badge">{{ total }}</span>
      </div>
      <NButton type="primary" @click="openUploadModal">
        <template #icon><NIcon><Add /></NIcon></template>
        上传文件
      </NButton>
    </div>

    <!-- Upload Modal -->
    <NModal v-model:show="showUploadModal" preset="card" title="上传文件"
      style="width: 90vw; max-width: 520px"
    >
      <div class="upload-modal-body">
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

        <!-- File queue -->
        <div v-if="uploadFiles.length > 0" class="upload-queue">
          <div class="upload-queue-header">
            <span>待上传：{{ uploadFiles.length }} 个文件</span>
            <NButton size="small" @click="uploadFiles = []">清空</NButton>
          </div>
          <div v-for="(f, i) in uploadFiles" :key="i" class="upload-queue-item">
            <span>📄 {{ f.name }} ({{ formatSize(f.size) }})</span>
            <NButton text size="tiny" type="error" @click="removeFile(i)">移除</NButton>
          </div>
          <NProgress v-if="uploading" type="line" :percentage="uploadProgress" :height="16" :border-radius="3" style="margin-top:8px" />
        </div>
      </div>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showUploadModal = false" :disabled="uploading">取消</NButton>
          <NButton type="primary" :loading="uploading" :disabled="uploadFiles.length === 0" @click="handleUpload">
            {{ uploading ? '上传中…' : '开始上传' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

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
        <NCard v-for="doc in docs" :key="doc.id" size="small" class="dm-card" hoverable>
          <div class="doc-card-header">
            <span class="doc-type-icon doc-card-icon" :style="{ color: getFileTypeConfig(doc.file_type).color }">
              {{ getFileTypeConfig(doc.file_type).icon }}
            </span>
            <div class="doc-card-title-wrap">
              <span
                class="doc-name doc-name-clickable"
                :title="doc.filename"
                @click.stop="openDetail(doc)"
                role="button"
                tabindex="0"
                @keydown.enter.prevent="openDetail(doc)"
                @keydown.space.prevent="openDetail(doc)"
              >{{ doc.filename }}</span>
            </div>
          </div>
          <div class="doc-card-meta">
            <NTag :type="statusColors[doc.status] as any" size="small">
              {{ statusLabels[doc.status] || doc.status }}
            </NTag>
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
        <NDescriptions bordered :column="1" size="small" label-style="width: 120px">
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
              @click="openChunks(detailDoc.id); showDetail = false"
              role="button"
              tabindex="0"
              @keydown.enter.prevent="openChunks(detailDoc.id); showDetail = false"
              @keydown.space.prevent="openChunks(detailDoc.id); showDetail = false"
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
              {{ detailDoc.kb_ids.length > 0 ? `${detailDoc.kb_ids.length} 个知识库` : '未关联' }}
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

/* Upload Modal */
.upload-modal-body { display: flex; flex-direction: column; gap: 12px; }
.upload-zone { border: 2px dashed var(--color-border); border-radius: 8px; padding: 28px; text-align: center; cursor: pointer; transition: all .2s; }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--color-primary); background: rgba(88,166,255,0.04); }
.upload-zone-content p { margin: 10px 0 4px; font-weight: 500; }
.upload-hint { font-size: 0.8rem; color: var(--color-text-muted); }

.upload-queue { padding: 12px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; }
.upload-queue-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 500; }
.upload-queue-item { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 0.85rem; }

/* Filters */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }

/* Doc list */
.dm-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}
.dm-card { transition: box-shadow .2s, border-color .2s; }
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
.kb-pick-card strong { display: block; font-size: var(--text-sm); margin-bottom: 2px; }
.kb-pick-desc { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-meta { font-size: 0.65rem; color: var(--color-text-muted); }
</style>