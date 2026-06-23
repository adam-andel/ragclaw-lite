<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import {
  NButton, NModal, NInput, NCard, NSpace, NTag, NEmpty,
  NPopconfirm, NIcon, NSelect, NSpin, NProgress, NDataTable,
  NCheckbox, useMessage,
} from 'naive-ui'
import { Add, Trash, People, Create, Search, Filter } from '@vicons/ionicons5'
import {
  listKnowledgeBases, createKnowledgeBase, deleteKnowledgeBase,
  updateKnowledgeBase, listKBDocuments, addDocumentsToKB,
  removeDocumentFromKB, listAllDocuments, getDocumentChunks,
  getDocumentStatus,
} from '@/api/documents'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase, DocumentItem, ChunkItem, DocumentListResponse } from '@/types'

const message = useMessage()
const kbs = ref<KnowledgeBase[]>([])
const selectedKbId = ref<string>('')
const documents = ref<DocumentItem[]>([])
const chunks = ref<ChunkItem[]>([])
const showChunksFor = ref(false)
const loadingDocs = ref(false)
const loadingKbs = ref(false)
const chunksLoading = ref(false)
const auth = useAuthStore()

// KB create
const showCreateKb = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const creating = ref(false)

// KB rename
const showRenameKb = ref(false)
const renameKbId = ref('')
const renameKbName = ref('')
const renameKbDesc = ref('')
const renaming = ref(false)

// Select documents modal
const showSelectDocs = ref(false)
const availableDocs = ref<DocumentItem[]>([])
const selectedDocIds = ref<string[]>([])
const loadingAvailableDocs = ref(false)
const availableTotal = ref(0)
const availablePage = ref(1)
const availableSearch = ref('')
const availableStatus = ref<string | null>(null)
const availableType = ref<string | null>(null)
const linkingDocs = ref(false)

// KB search & sort
const kbSearch = ref('')
const kbSortBy = ref<'name' | 'doc_count' | 'recent'>('recent')

const kbSortOptions = [
  { label: '最近更新', value: 'recent' },
  { label: '名称', value: 'name' },
  { label: '文档数量', value: 'doc_count' },
]

const filteredKbs = computed(() => {
  let list = [...kbs.value]
  // filter by search
  if (kbSearch.value.trim()) {
    const q = kbSearch.value.trim().toLowerCase()
    list = list.filter(kb =>
      kb.name.toLowerCase().includes(q) ||
      (kb.description && kb.description.toLowerCase().includes(q))
    )
  }
  // sort
  list.sort((a, b) => {
    switch (kbSortBy.value) {
      case 'name':
        return a.name.localeCompare(b.name, 'zh-CN')
      case 'doc_count':
        return b.doc_count - a.doc_count
      case 'recent':
      default:
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    }
  })
  return list
})

onMounted(() => loadKBs())

async function loadKBs() {
  loadingKbs.value = true
  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    if (selectedKbId.value && !kbs.value.find(k => k.id === selectedKbId.value)) {
      selectedKbId.value = ''
      documents.value = []
    }
  } catch (e: any) {
    message.error('加载知识库失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loadingKbs.value = false
  }
}

async function handleCreateKb() {
  if (!newKbName.value.trim()) return
  creating.value = true
  try {
    await createKnowledgeBase({ name: newKbName.value, description: newKbDesc.value })
    await loadKBs()
    showCreateKb.value = false
    newKbName.value = ''
    newKbDesc.value = ''
    message.success('知识库创建成功')
  } catch (e: any) {
    message.error('创建失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    creating.value = false
  }
}

function openRename(kb: KnowledgeBase, e: Event) {
  e.stopPropagation()
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
    if (selectedKbId.value === id) {
      selectedKbId.value = ''
      documents.value = []
    }
    await loadKBs()
    message.success('知识库已删除')
  } catch (e: any) {
    message.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

async function selectKb(id: string) {
  selectedKbId.value = id
  await loadDocuments()
}

async function loadDocuments() {
  if (!selectedKbId.value) return
  loadingDocs.value = true
  try {
    const res = await listKBDocuments(selectedKbId.value)
    documents.value = res.data || []
  } catch (e: any) {
    message.error('加载文档失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loadingDocs.value = false
  }
}

// Poll processing progress
let pollTimer: ReturnType<typeof setInterval> | null = null
watch(selectedKbId, (newId, oldId) => {
  if (oldId && pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (newId) {
    pollTimer = setInterval(async () => {
      const processing = documents.value.filter(d =>
        ['pending', 'parsing', 'chunking', 'embedding'].includes(d.status)
      )
      if (processing.length === 0) return
      // Refresh individual document statuses
      for (const doc of processing) {
        try {
          const res = await getDocumentStatus(doc.id)
          const data = res.data
          Object.assign(doc, {
            status: data.status,
            progress: data.progress,
            error_message: data.error_message,
            chunk_count: data.chunk_count,
          })
        } catch { /* ignore */ }
      }
    }, 3000)
  }
})

onUnmounted(() => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
})

// ── Select Documents Modal ──

async function openSelectDocs() {
  showSelectDocs.value = true
  selectedDocIds.value = []
  availablePage.value = 1
  availableSearch.value = ''
  availableStatus.value = 'completed'
  await loadAvailableDocs()
}

async function loadAvailableDocs() {
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
  if (selectedDocIds.value.length === 0) return
  linkingDocs.value = true
  try {
    const res = await addDocumentsToKB(selectedKbId.value, selectedDocIds.value)
    message.success(`已添加 ${res.data.added} 个文档${res.data.skipped > 0 ? '，跳过 ' + res.data.skipped + ' 个' : ''}`)
    showSelectDocs.value = false
    await loadDocuments()
  } catch (e: any) {
    message.error('添加失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    linkingDocs.value = false
  }
}

async function handleRemoveDoc(docId: string) {
  try {
    await removeDocumentFromKB(selectedKbId.value, docId)
    documents.value = documents.value.filter(d => d.id !== docId)
    message.success('文档已从知识库移除')
  } catch (e: any) {
    message.error('移除失败：' + (e?.response?.data?.detail || e.message))
  }
}

// ── Chunks ──

async function showChunks(docId: string) {
  chunksLoading.value = true
  showChunksFor.value = true
  try {
    const res = await getDocumentChunks(docId)
    chunks.value = res.data
  } catch {
    message.error('加载分块失败')
    showChunksFor.value = false
  } finally {
    chunksLoading.value = false
  }
}
// ── Sharing ──

const showShare = ref(false)
const shareKbId = ref('')
const shareUsers = ref<any[]>([])
const shareAddUser = ref('')
const allUsers = ref<any[]>([])
const shareLoading = ref(false)

const allUserOptions = computed(() =>
  allUsers.value
    .filter((u: any) => !shareUsers.value.some((s: any) => s.id === u.id))
    .map((u: any) => ({ label: `${u.display_name || u.username} (${u.username})`, value: u.id }))
)

async function openShare(kbId: string) {
  shareKbId.value = kbId
  shareLoading.value = true
  try {
    const r = await client.get(`/kb/${kbId}/users`)
    shareUsers.value = r.data
  } catch { shareUsers.value = [] }
  try {
    const r = await client.get('/users')
    allUsers.value = r.data
  } catch { allUsers.value = [] }
  shareLoading.value = false
  showShare.value = true
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

// ── Helpers ──

const selectedKb = computed(() => kbs.value.find(k => k.id === selectedKbId.value))

const statusColors: Record<string, string> = {
  pending: 'default', uploaded: 'default',
  parsing: 'warning', chunking: 'warning',
  embedding: 'info', completed: 'success', failed: 'error',
}
const statusLabels: Record<string, string> = {
  pending: '等待中', uploaded: '已上传',
  parsing: '解析中', chunking: '分块中',
  embedding: '向量化', completed: '已完成', failed: '失败',
}

const fileTypeOptions = [
  { label: '全部类型', value: null },
  { label: 'PDF', value: 'pdf' },
  { label: 'Word', value: 'docx' },
  { label: 'Markdown', value: 'md' },
  { label: '文本', value: 'txt' },
]

const docStatusOptions = [
  { label: '已完成', value: 'completed' },
  { label: '处理中', value: 'pending' },
  { label: '失败', value: 'failed' },
  { label: '全部', value: null },
]

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

// File type → icon + color
const fileTypeConfig: Record<string, { icon: string; color: string; label: string }> = {
  pdf: { icon: '📕', color: '#ef4444', label: 'PDF' },
  docx: { icon: '📘', color: '#3b82f6', label: 'Word' },
  doc: { icon: '📘', color: '#3b82f6', label: 'Word' },
  md: { icon: '📗', color: '#22c55e', label: 'MD' },
  txt: { icon: '📄', color: '#64748b', label: 'TXT' },
  csv: { icon: '📊', color: '#f59e0b', label: 'CSV' },
  pptx: { icon: '📙', color: '#f97316', label: 'PPT' },
  xlsx: { icon: '📈', color: '#22c55e', label: 'Excel' },
}

function getFileTypeConfig(ext: string) {
  return fileTypeConfig[ext.toLowerCase()] || { icon: '📄', color: '#64748b', label: ext.toUpperCase() }
}

const processingStatuses = ['pending', 'parsing', 'chunking', 'embedding']

function isProcessing(status: string) {
  return processingStatuses.includes(status)
}
</script>
<template>
  <div class="kb-view">
    <div class="kb-header">
      <h2>🗂️ 知识库管理</h2>
      <NButton type="primary" size="small" @click="showCreateKb = true">
        <template #icon><NIcon><Add /></NIcon></template>
        新建知识库
      </NButton>
    </div>

    <div class="kb-body">
      <!-- KB List -->
      <div class="kb-list" role="list" aria-label="知识库列表">
        <!-- Search & sort bar -->
        <div v-if="!loadingKbs && kbs.length > 0" class="kb-list-toolbar">
          <NInput
            v-model:value="kbSearch"
            placeholder="搜索知识库…"
            size="small"
            clearable
          >
            <template #prefix><NIcon size="16"><Search /></NIcon></template>
          </NInput>
          <NSelect
            v-model:value="kbSortBy"
            :options="kbSortOptions"
            size="small"
            style="width:100%"
          />
        </div>

        <!-- Card list (scrollable) -->
        <div class="kb-cards">
          <NSpin :show="loadingKbs" v-if="loadingKbs || kbs.length === 0" />
          <NEmpty v-if="!loadingKbs && kbs.length === 0" description="暂无知识库" />
          <NEmpty v-if="!loadingKbs && kbs.length > 0 && filteredKbs.length === 0" description="无匹配的知识库" />
          <NCard
            v-for="kb in filteredKbs" :key="kb.id"
            :class="['kb-card', { active: kb.id === selectedKbId }]"
            size="small" role="button" tabindex="0"
            :aria-selected="kb.id === selectedKbId"
            :aria-label="`知识库：${kb.name}`"
            @click="selectKb(kb.id)"
            @keydown.enter.prevent="selectKb(kb.id)"
            @keydown.space.prevent="selectKb(kb.id)"
          >
            <div class="kb-card-header">
              <strong class="kb-card-name">{{ kb.name }}</strong>
              <NButton text size="tiny" @click="openRename(kb, $event)" title="改名">
                <template #icon><NIcon size="14"><Create /></NIcon></template>
              </NButton>
            </div>
            <div v-if="kb.description" class="kb-card-desc">{{ kb.description }}</div>
            <div class="kb-card-meta">
              <span class="kb-card-stat">📄 {{ kb.doc_count }}</span>
              <span class="kb-card-stat">🧬 {{ kb.vector_count }}</span>
            </div>
          </NCard>
        </div>
      </div>

      <!-- Documents -->
      <div class="kb-docs">
        <div v-if="!selectedKbId" class="empty-hint">
          <NEmpty description="选择一个知识库查看文档" />
        </div>
        <template v-else>
          <div class="docs-header">
            <h3>{{ selectedKb?.name ?? '未知知识库' }} · 文档 ({{ documents.length }})</h3>
            <NSpace>
              <NButton v-if="auth.isStaff" size="small" @click="openShare(selectedKbId)">
                <template #icon><NIcon><People /></NIcon></template>
                共享
              </NButton>
              <NButton type="primary" size="small" @click="openSelectDocs">
                <template #icon><NIcon><Search /></NIcon></template>
                选择文档
              </NButton>
              <NPopconfirm @positive-click="handleDeleteKb(selectedKbId)">
                <template #trigger>
                  <NButton size="small" text type="error">
                    <template #icon><NIcon><Trash /></NIcon></template>
                    删除
                  </NButton>
                </template>
                确定删除「{{ selectedKb?.name }}」？文档不会被删除，仅解除关联。
              </NPopconfirm>
            </NSpace>
          </div>

          <NSpin :show="loadingDocs">
            <NEmpty v-if="!loadingDocs && documents.length === 0" description="暂无文档，点击「选择文档」添加" />
            <NCard v-for="doc in documents" :key="doc.id" size="small" class="doc-card"
              tabindex="0" role="listitem"
              :aria-label="`文档：${doc.filename}，状态：${statusLabels[doc.status] || doc.status}`"
            >
              <!-- Row 1: file icon + name + status + actions -->
              <div class="doc-row-top">
                <div class="doc-name-group">
                  <span class="doc-type-icon" :style="{ color: getFileTypeConfig(doc.file_type).color }">
                    {{ getFileTypeConfig(doc.file_type).icon }}
                  </span>
                  <span class="doc-name">{{ doc.filename }}</span>
                  <NTag :type="statusColors[doc.status] as any" size="small">
                    {{ statusLabels[doc.status] || doc.status }}
                  </NTag>
                  <NTag v-if="doc.status === 'failed' && doc.error_message" size="small" type="error" class="doc-error-tag">
                    {{ doc.error_message }}
                  </NTag>
                </div>
                <div class="doc-actions">
                  <NButton text size="tiny" @click="showChunks(doc.id)">查看分块</NButton>
                  <NPopconfirm @positive-click="handleRemoveDoc(doc.id)">
                    <template #trigger>
                      <NButton text size="tiny" type="warning">移除</NButton>
                    </template>
                    从知识库移除「{{ doc.filename }}」？（文档本身不会被删除）
                  </NPopconfirm>
                </div>
              </div>
              <!-- Row 2: progress bar (processing) or metadata (completed/failed) -->
              <div v-if="isProcessing(doc.status)" class="doc-row-bottom">
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
              <div v-else class="doc-row-bottom doc-meta">
                <span class="doc-meta-label">{{ getFileTypeConfig(doc.file_type).label }}</span>
                <span class="doc-meta-sep">·</span>
                <span>{{ formatSize(doc.file_size) }}</span>
                <template v-if="doc.chunk_count > 0">
                  <span class="doc-meta-sep">·</span>
                  <span>{{ doc.chunk_count }} 分块</span>
                </template>
              </div>
            </NCard>
          </NSpin>
        </template>
      </div>
    </div>

    <!-- Chunks Modal -->
    <NModal v-model:show="showChunksFor" title="分块预览" style="max-width: 95vw; width: 800px">
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
          <NButton @click="showChunksFor = false">关闭</NButton>
        </div>
      </NSpin>
    </NModal>

    <!-- Create KB Modal -->
    <NModal v-model:show="showCreateKb" title="新建知识库">
      <div class="kb-form">
        <NInput v-model:value="newKbName" placeholder="知识库名称" />
        <NInput v-model:value="newKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2 }" />
        <NButton type="primary" :loading="creating" @click="handleCreateKb" block>创建</NButton>
      </div>
    </NModal>

    <!-- Rename KB Modal -->
    <NModal v-model:show="showRenameKb" title="编辑知识库">
      <div class="kb-form">
        <NInput v-model:value="renameKbName" placeholder="知识库名称" />
        <NInput v-model:value="renameKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2 }" />
        <NButton type="primary" :loading="renaming" @click="handleRenameKb" block>保存</NButton>
      </div>
    </NModal>

    <!-- Select Documents Modal -->
    <NModal v-model:show="showSelectDocs" title="选择文档加入知识库" style="width: 90vw; max-width: 1000px">
      <div class="select-docs-modal">
        <div class="select-docs-filters">
          <NInput v-model:value="availableSearch" placeholder="搜索文件名…" clearable @keyup.enter="loadAvailableDocs" style="flex:1">
            <template #prefix><NIcon><Search /></NIcon></template>
          </NInput>
          <NSelect v-model:value="availableStatus" :options="docStatusOptions" placeholder="状态" style="width:110px" @update:value="loadAvailableDocs" />
          <NSelect v-model:value="availableType" :options="fileTypeOptions" placeholder="类型" style="width:110px" @update:value="loadAvailableDocs" />
          <NButton @click="loadAvailableDocs" secondary>筛选</NButton>
        </div>
        <NSpin :show="loadingAvailableDocs">
          <NEmpty v-if="!loadingAvailableDocs && availableDocs.length === 0" description="没有可用的已完成文档，请先在文档管理页面上传并处理" />
          <div class="select-docs-list" v-if="availableDocs.length > 0">
            <div v-for="doc in availableDocs" :key="doc.id"
              :class="['select-doc-row', { selected: selectedDocIds.includes(doc.id) }]"
              @click="selectedDocIds.includes(doc.id)
                ? selectedDocIds = selectedDocIds.filter(id => id !== doc.id)
                : selectedDocIds.push(doc.id)"
            >
              <NCheckbox :checked="selectedDocIds.includes(doc.id)" style="flex-shrink:0" />
              <span class="select-doc-name">📄 {{ doc.filename }}</span>
              <NSpace size="small">
                <NTag size="small">{{ doc.file_type.toUpperCase() }}</NTag>
                <NTag size="small">{{ formatSize(doc.file_size) }}</NTag>
                <NTag size="small" type="success">已完成</NTag>
              </NSpace>
            </div>
          </div>
        </NSpin>
        <div class="select-docs-actions">
          <span class="select-docs-count">已选 {{ selectedDocIds.length }}{{ availableTotal ? ' / 共 ' + availableTotal + ' 个文档' : '' }}</span>
          <NSpace>
            <NButton @click="showSelectDocs = false">取消</NButton>
            <NButton type="primary" :disabled="selectedDocIds.length === 0" :loading="linkingDocs" @click="handleSelectDocs">
              加入知识库
            </NButton>
          </NSpace>
        </div>
      </div>
    </NModal>

    <!-- Share Modal -->
    <NModal v-model:show="showShare" title="共享管理"
      style="width:70vw; max-width:1000px; height:70vh; max-height:800px"
      :title-style="{ fontSize: '1.25rem', fontWeight: 'bold' }"
    >
      <div class="share-form">
        <NSpin :show="shareLoading">
          <div class="share-add-row">
            <NSelect v-model:value="shareAddUser" :options="allUserOptions"
              placeholder="搜索用户…" filterable clearable size="large" style="flex:1"
            />
            <NButton type="primary" size="large" :disabled="!shareAddUser" @click="addKbUser(shareAddUser)">
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
  </div>
</template>
<style scoped>
.kb-view { display: flex; flex-direction: column; height: 100%; }
.kb-header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 16px; border-bottom: 1px solid var(--color-border); flex-shrink: 0; }
.kb-header h2 { font-size: 1.25rem; }
.kb-body { display: flex; gap: 24px; flex: 1; overflow: hidden; padding-top: 16px; }
.kb-list { width: 220px; flex-shrink: 0; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.kb-list-toolbar { display: flex; flex-direction: column; gap: 6px; padding-bottom: 10px; border-bottom: 1px solid var(--color-border); margin-bottom: 8px; flex-shrink: 0; }
.kb-cards { flex: 1; overflow-y: auto; min-height: 0; }
.kb-docs { flex: 1; overflow-y: auto; }

/* KB Card */
.kb-card { margin-bottom: 8px; cursor: pointer; transition: border-color .2s; user-select: none; }
.kb-card:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -2px; border-radius: var(--radius); }
.kb-card.active { border-color: var(--color-primary); }
.kb-card-header { display: flex; justify-content: space-between; align-items: center; }
.kb-card-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-card-desc { font-size: var(--text-xs); color: var(--color-text-muted); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-card-meta { margin-top: 6px; display: flex; gap: 8px; }
.kb-card-stat { font-size: 0.65rem; color: var(--color-text-muted); }

/* Docs */
.docs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.doc-card { margin-bottom: 8px; transition: box-shadow .2s, border-color .2s; }
.doc-card:hover { box-shadow: var(--shadow-sm); }
.doc-card:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -1px; border-radius: var(--radius); }

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

.empty-hint { display: flex; align-items: center; justify-content: center; height: 100%; }

/* Chunks Modal */
.chunks-modal { max-height: 60vh; overflow-y: auto; }
.chunk-card { margin-bottom: 8px; }
.chunk-meta { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.chunk-content { white-space: pre-wrap; word-break: break-word; font-size: var(--text-sm); line-height: 1.6; max-height: 200px; overflow-y: auto; }

/* KB Form */
.kb-form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; min-width: min(350px, 80vw); }

/* Select Docs Modal */
.select-docs-modal { display: flex; flex-direction: column; gap: 12px; max-height: 70vh; }
.select-docs-filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.select-docs-list { max-height: 50vh; overflow-y: auto; }
.select-doc-row { display: flex; align-items: center; gap: 10px; padding: 10px 8px; cursor: pointer; border-bottom: 1px solid var(--color-border); transition: background .15s; }
.select-doc-row:hover { background: rgba(88, 166, 255, 0.04); }
.select-doc-row.selected { background: rgba(88, 166, 255, 0.1); }
.select-doc-name { font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.select-docs-actions { display: flex; justify-content: space-between; align-items: center; padding-top: 8px; border-top: 1px solid var(--color-border); }
.select-docs-count { font-size: var(--text-sm); color: var(--color-text-muted); }

/* Share Form */
.share-form { padding: 20px 24px; background: var(--color-surface); border-radius: 12px; height: 100%; box-sizing: border-box; color: var(--color-text); }
.share-add-row { display: flex; gap: 8px; margin-bottom: 20px; }
.share-empty { padding: 20px 0; }
.share-list { max-height: calc(100% - 80px); overflow-y: auto; }
.share-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 8px; border-bottom: 1px solid var(--color-border); transition: background .15s; }
.share-row:hover { background: rgba(88, 166, 255, 0.04); }
.share-user-info { display: flex; align-items: center; gap: 10px; }
.share-user-avatar { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--color-border); border-radius: 50%; font-size: 0.9rem; }
.share-user-name { font-weight: 500; font-size: 0.9rem; }
.share-user-sub { font-size: 0.75rem; color: var(--color-text-muted); }

/* Responsive */
@media (max-width: 767px) {
  .kb-body { flex-direction: column; }
  .kb-list { width: 100%; max-height: none; flex-shrink: 0; }
  .kb-list-toolbar { flex-direction: row; gap: 8px; padding-bottom: 10px; }
  .kb-list-toolbar :deep(.n-input) { flex: 1; min-width: 0; }
  .kb-list-toolbar :deep(.n-base-selection) { width: 120px; flex-shrink: 0; }
  .kb-cards { display: flex; gap: 8px; overflow-x: auto; overflow-y: hidden; padding-bottom: 4px; flex: none; }
  .kb-cards :deep(.n-card) { min-width: 160px; flex-shrink: 0; margin-bottom: 0; }
  .kb-docs { flex: 1; overflow-y: auto; }
  .docs-header { flex-direction: column; align-items: flex-start; }
}
</style>