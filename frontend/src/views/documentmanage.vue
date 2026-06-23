<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import {
  NButton, NTag, NSpace, NSpin, NEmpty, NProgress,
  NInput, NSelect, NPagination, NPopconfirm, useMessage,
  NIcon, NModal, NCard,
} from 'naive-ui'
import { CloudUpload, Search, Trash } from '@vicons/ionicons5'
import {
  uploadDocument, uploadDocumentsBatch, listAllDocuments,
  getDocumentStatus, getDocumentChunks, deleteDocument,
} from '@/api/documents'
import { useAuthStore } from '@/stores/auth'
import type { DocumentItem, ChunkItem } from '@/types'

const message = useMessage()
const auth = useAuthStore()

// List state
const docs = ref<DocumentItem[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const loading = ref(false)
const search = ref('')
const filterStatus = ref<string | null>(null)
const filterType = ref<string | null>(null)

// Upload state
const uploading = ref(false)
const uploadFiles = ref<File[]>([])
const uploadProgress = ref(0)
const dragOver = ref(false)

// Chunks modal
const showChunks = ref(false)
const chunks = ref<ChunkItem[]>([])
const chunksLoading = ref(false)

// Progress polling
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => { loadDocs(); startPolling() })

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
  input.accept = '.pdf,.docx,.md,.txt'
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

async function openChunks(docId: string) {
  chunksLoading.value = true
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

const typeOptions = [
  { label: '全部类型', value: null },
  { label: 'PDF', value: 'pdf' }, { label: 'Word', value: 'docx' },
  { label: 'Markdown', value: 'md' }, { label: '文本', value: 'txt' },
]
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
</script>
<template>
  <div class="dm-view">
    <div class="dm-header">
      <h2>📁 文档管理</h2>
    </div>

    <!-- Upload Zone -->
    <div :class="['upload-zone', { dragover: dragOver }]"
      @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop"
      @click="triggerFileSelect"
    >
      <div class="upload-zone-content">
        <NIcon size="32" color="var(--color-primary)"><CloudUpload /></NIcon>
        <p>点击或拖拽文件到此处上传</p>
        <span class="upload-hint">支持 PDF、Word、Markdown、TXT，单文件最大 50MB</span>
      </div>
    </div>

    <!-- File queue -->
    <div v-if="uploadFiles.length > 0" class="upload-queue">
      <div class="upload-queue-header">
        <span>待上传：{{ uploadFiles.length }} 个文件</span>
        <NSpace>
          <NButton size="small" @click="uploadFiles = []">清空</NButton>
          <NButton type="primary" size="small" :loading="uploading" @click="handleUpload">开始上传</NButton>
        </NSpace>
      </div>
      <div v-for="(f, i) in uploadFiles" :key="i" class="upload-queue-item">
        <span>📄 {{ f.name }} ({{ formatSize(f.size) }})</span>
        <NButton text size="tiny" type="error" @click="removeFile(i)">移除</NButton>
      </div>
      <NProgress v-if="uploading" type="line" :percentage="uploadProgress" :height="16" :border-radius="3" />
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
        <NCard v-for="doc in docs" :key="doc.id" size="small" class="dm-card">
          <div class="dm-card-row">
            <div class="dm-card-info">
              <span class="dm-card-name">📄 {{ doc.filename }}</span>
              <NSpace size="small">
                <NTag :type="statusColors[doc.status] as any" size="small">{{ statusLabels[doc.status] || doc.status }}</NTag>
                <NProgress
                  v-if="['pending','parsing','chunking','embedding'].includes(doc.status)"
                  type="line" :percentage="doc.progress" :height="14" :border-radius="3" style="width:80px"
                />
                <NTag size="small">{{ doc.file_type.toUpperCase() }}</NTag>
                <NTag size="small">{{ formatSize(doc.file_size) }}</NTag>
                <NTag size="small" v-if="doc.chunk_count > 0">{{ doc.chunk_count }} 分块</NTag>
                <NTag size="small" type="error" v-if="doc.status === 'failed'" :title="doc.error_message">⚠ 失败</NTag>
              </NSpace>
            </div>
            <div class="dm-card-meta">
              <span class="dm-kb-tags" v-if="doc.kb_ids.length > 0">
                {{ doc.kb_ids.length }} 个知识库
              </span>
              <span class="dm-kb-tags muted" v-else>未关联</span>
              <span class="dm-date">{{ new Date(doc.created_at).toLocaleDateString('zh-CN') }}</span>
            </div>
            <div class="dm-card-actions">
              <NButton text size="tiny" @click="openChunks(doc.id)">查看分块</NButton>
              <NPopconfirm @positive-click="handleDelete(doc.id)">
                <template #trigger><NButton text size="tiny" type="error">删除</NButton></template>
                确定删除文档「{{ doc.filename }}」？将从所有知识库中移除。
              </NPopconfirm>
            </div>
          </div>
        </NCard>
      </div>
    </NSpin>

    <div class="dm-pagination" v-if="total > size">
      <NPagination :page="page" :page-size="size" :item-count="total" @update:page="onPageChange" />
    </div>

    <!-- Chunks Modal -->
    <NModal v-model:show="showChunks" title="分块预览" style="max-width: 95vw; width: 800px">
      <NSpin :show="chunksLoading">
        <div class="chunks-modal">
          <NEmpty v-if="!chunksLoading && chunks.length === 0" description="暂无分块数据" />
          <NCard v-for="c in chunks" :key="c.id" size="small" class="chunk-card">
            <div class="chunk-meta">
              <NTag size="tiny">#{{ c.chunk_index }}</NTag>
              <NTag size="tiny" v-if="c.heading">{{ c.heading }}</NTag>
              <span>{{ c.token_count }} tokens</span>
            </div>
            <p class="chunk-content">{{ c.content }}</p>
          </NCard>
          <NButton @click="showChunks = false">关闭</NButton>
        </div>
      </NSpin>
    </NModal>
  </div>
</template>
<style scoped>
.dm-view { height: 100%; overflow-y: auto; }
.dm-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.dm-header h2 { font-size: 1.25rem; }

/* Upload Zone */
.upload-zone { border: 2px dashed var(--color-border); border-radius: 8px; padding: 24px; text-align: center; cursor: pointer; transition: all .2s; margin-bottom: 16px; }
.upload-zone:hover, .upload-zone.dragover { border-color: var(--color-primary); background: rgba(88,166,255,0.04); }
.upload-zone-content p { margin: 8px 0 4px; font-weight: 500; }
.upload-hint { font-size: 0.8rem; color: var(--color-text-muted); }

.upload-queue { margin-bottom: 16px; padding: 12px; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: 8px; }
.upload-queue-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-weight: 500; }
.upload-queue-item { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 0.85rem; }

/* Filters */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }

/* Doc list */
.dm-list { display: flex; flex-direction: column; gap: 8px; }
.dm-card-row { display: flex; flex-direction: column; gap: 6px; }
.dm-card-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dm-card-name { font-weight: 500; }
.dm-card-meta { display: flex; gap: 12px; font-size: 0.75rem; color: var(--color-text-muted); }
.dm-kb-tags { color: var(--color-primary); }
.dm-kb-tags.muted { color: var(--color-text-muted); }
.dm-card-actions { display: flex; gap: 8px; }
.dm-date { color: var(--color-text-muted); }

.dm-pagination { display: flex; justify-content: center; margin-top: 16px; padding-bottom: 24px; }

/* Chunks */
.chunks-modal { max-height: 60vh; overflow-y: auto; }
.chunk-card { margin-bottom: 8px; }
.chunk-meta { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.chunk-content { white-space: pre-wrap; word-break: break-word; font-size: var(--text-sm); line-height: 1.6; max-height: 200px; overflow-y: auto; }
</style>