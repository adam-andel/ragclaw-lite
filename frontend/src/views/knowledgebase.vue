<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { NButton, NModal, NInput, NCard, NSpace, NTag, NEmpty, NPopconfirm, NIcon, NSelect, NSpin, NProgress, useMessage } from 'naive-ui'
import { Add, Trash, CloudUpload, People } from '@vicons/ionicons5'
import { listKnowledgeBases, createKnowledgeBase, deleteKnowledgeBase, uploadDocument, listDocuments, getDocumentChunks, deleteDocument, getDocumentStatus } from '@/api/documents'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase, DocumentItem, ChunkItem } from '@/types'

const message = useMessage()
const kbs = ref<KnowledgeBase[]>([])
const selectedKbId = ref<string>('')
const documents = ref<DocumentItem[]>([])
const chunks = ref<ChunkItem[]>([])
const showChunksFor = ref(false)
const loadingDocs = ref(false)
const loadingKbs = ref(false)

// Upload state
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadFileName = ref('')

// Chunks loading
const chunksLoading = ref(false)

const showCreateKb = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const creating = ref(false)
const auth = useAuthStore()

onMounted(() => loadKBs())

async function loadKBs() {
  loadingKbs.value = true
  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    // 校验当前选中的 KB 是否仍然存在
    if (selectedKbId.value && !kbs.value.find(k => k.id === selectedKbId.value)) {
      selectedKbId.value = ''
      documents.value = []
    }
  } catch (e: any) {
    message.error('加载知识库失败：' + (e?.response?.data?.detail || e.message || '请检查网络连接'))
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

function triggerFileUpload() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.pdf,.docx,.md,.txt'
  input.setAttribute('aria-label', '选择要上传的文档文件')
  input.onchange = (e: Event) => {
    const file = (e.target as HTMLInputElement).files?.[0]
    if (file) {
      // 文件大小校验：限制 50MB
      const maxSize = 50 * 1024 * 1024
      if (file.size > maxSize) {
        message.warning(`文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大支持 50MB`)
        return
      }
      handleUpload(file)
    }
  }
  input.click()
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
    const res = await listDocuments(selectedKbId.value)
    documents.value = res.data
  } catch (e: any) {
    message.error('加载文档列表失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    loadingDocs.value = false
  }
}

async function handleUpload(file: File) {
  if (!selectedKbId.value) {
    message.warning('请先选择一个知识库')
    return
  }
  uploading.value = true
  uploadProgress.value = 0
  uploadFileName.value = file.name
  try {
    await uploadDocument(selectedKbId.value, file, (pct: number) => {
      uploadProgress.value = pct
    })
    message.success('上传成功，正在解析文档…')
    // 轮询文档状态直到完成
    await pollDocumentStatus()
    await loadDocuments()
  } catch (e: any) {
    message.error('上传失败：' + (e?.response?.data?.detail || e.message))
  } finally {
    uploading.value = false
    uploadFileName.value = ''
  }
}

async function pollDocumentStatus() {
  // 等待文档列表刷新后获取最新文档，轮询直到全部完成
  const maxAttempts = 30
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      const res = await listDocuments(selectedKbId.value)
      documents.value = res.data
      const pending = res.data.some((d: DocumentItem) =>
        ['uploaded', 'parsing', 'chunking', 'embedding'].includes(d.status)
      )
      if (!pending) break
    } catch {
      // 轮询失败继续重试
    }
  }
}

async function handleDeleteDoc(id: string) {
  try {
    await deleteDocument(id)
    await loadDocuments()
    message.success('文档已删除')
  } catch (e: any) {
    message.error('删除失败：' + (e?.response?.data?.detail || e.message))
  }
}

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

// ---- Sharing ----
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
  } catch (e: any) {
    message.error('加载共享用户失败')
    shareUsers.value = []
  }
  try {
    const r = await client.get('/users')
    allUsers.value = r.data
  } catch {
    allUsers.value = []
  }
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

const selectedKb = computed(() => kbs.value.find((k) => k.id === selectedKbId.value))

const statusColors: Record<string, string> = {
  uploaded: 'default', parsing: 'warning', chunking: 'warning', embedding: 'info', completed: 'success', failed: 'error',
}
const statusLabels: Record<string, string> = {
  uploaded: '已上传', parsing: '解析中', chunking: '分块中', embedding: '向量化', completed: '已完成', failed: '失败',
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
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
        <NSpin :show="loadingKbs" v-if="loadingKbs || kbs.length === 0" />
        <NEmpty v-if="!loadingKbs && kbs.length === 0" description="暂无知识库" />
        <NCard
          v-for="kb in kbs"
          :key="kb.id"
          :class="['kb-card', { active: kb.id === selectedKbId }]"
          size="small"
          role="button"
          tabindex="0"
          :aria-selected="kb.id === selectedKbId"
          :aria-label="`知识库：${kb.name}`"
          @click="selectKb(kb.id)"
          @keydown.enter.prevent="selectKb(kb.id)"
          @keydown.space.prevent="selectKb(kb.id)"
        >
          <div class="kb-card-header">
            <strong class="kb-card-name">{{ kb.name }}</strong>
          </div>
          <div v-if="kb.description" class="kb-card-desc">{{ kb.description }}</div>
          <div class="kb-card-meta">
            <span class="kb-card-date">{{ new Date(kb.created_at).toLocaleDateString('zh-CN') }}</span>
          </div>
        </NCard>
      </div>

      <!-- Documents -->
      <div class="kb-docs">
        <div v-if="!selectedKbId" class="empty-hint">
          <NEmpty description="选择一个知识库查看文档" />
        </div>
        <template v-else>
          <div class="docs-header">
            <h3>{{ selectedKb?.name ?? '未知知识库' }} · 文档</h3>
            <NSpace>
              <NButton v-if="auth.isStaff" size="small" @click="openShare(selectedKbId)">
                <template #icon><NIcon><People /></NIcon></template>
                共享
              </NButton>
              <NButton type="primary" size="small" @click="triggerFileUpload" :loading="uploading">
                <template #icon><NIcon><CloudUpload /></NIcon></template>
                上传文档
              </NButton>
              <NPopconfirm @positive-click="handleDeleteKb(selectedKbId)">
                <template #trigger>
                  <NButton size="small" text type="error">
                    <template #icon><NIcon><Trash /></NIcon></template>
                    删除
                  </NButton>
                </template>
                确定删除「{{ selectedKb?.name }}」及其所有文档？
              </NPopconfirm>
            </NSpace>
          </div>

          <!-- Upload progress -->
          <div v-if="uploading" class="upload-progress">
            <span class="upload-progress-label">上传中：{{ uploadFileName }}</span>
            <NProgress
              type="line"
              :percentage="uploadProgress"
              :indicator-placement="'inside'"
              :height="20"
              :border-radius="4"
            />
          </div>

          <NSpin :show="loadingDocs">
            <NEmpty v-if="!loadingDocs && documents.length === 0" description="暂无文档" />
            <NCard
              v-for="doc in documents"
              :key="doc.id"
              size="small"
              class="doc-card"
              tabindex="0"
              role="listitem"
              :aria-label="`文档：${doc.filename}，状态：${statusLabels[doc.status] || doc.status}`"
            >
              <div class="doc-info">
                <span class="doc-name">📄 {{ doc.filename }}</span>
                <NSpace size="small">
                  <NTag :type="statusColors[doc.status] as any" size="small">
                    {{ statusLabels[doc.status] || doc.status }}
                  </NTag>
                  <NTag size="small">{{ formatSize(doc.file_size) }}</NTag>
                  <NTag size="small" v-if="doc.chunk_count > 0">{{ doc.chunk_count }} 分块</NTag>
                </NSpace>
              </div>
              <div class="doc-actions">
                <NButton text size="tiny" @click="showChunks(doc.id)">查看分块</NButton>
                <NPopconfirm @positive-click="handleDeleteDoc(doc.id)">
                  <template #trigger>
                    <NButton text size="tiny" type="error">删除</NButton>
                  </template>
                  确定删除文档「{{ doc.filename }}」？
                </NPopconfirm>
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
      <div class="create-kb-form">
        <NInput v-model:value="newKbName" placeholder="知识库名称" />
        <NInput v-model:value="newKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2 }" />
        <NButton type="primary" :loading="creating" @click="handleCreateKb" block>创建</NButton>
      </div>
    </NModal>

    <!-- Share Modal -->
    <NModal
      v-model:show="showShare"
      title="共享管理"
      style="width:70vw; max-width:1000px; height:70vh; max-height:800px"
      :title-style="{ fontSize: '1.25rem', fontWeight: 'bold' }"
    >
      <div class="share-form">
        <NSpin :show="shareLoading">
          <div class="share-add-row">
            <NSelect
              v-model:value="shareAddUser"
              :options="allUserOptions"
              placeholder="搜索用户…"
              filterable
              clearable
              size="large"
              style="flex:1"
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
.kb-list { width: 220px; overflow-y: auto; flex-shrink: 0; }
.kb-docs { flex: 1; overflow-y: auto; }

/* KB Card */
.kb-card {
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color .2s;
  user-select: none;
}
.kb-card:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -2px;
  border-radius: var(--radius);
}
.kb-card.active { border-color: var(--color-primary); }
.kb-card-header { display: flex; justify-content: space-between; align-items: center; }
.kb-card-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-card-desc {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-card-meta {
  margin-top: 6px;
}
.kb-card-date {
  font-size: 0.65rem;
  color: var(--color-text-muted);
}

/* Docs */
.docs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.doc-card { margin-bottom: 8px; }
.doc-card:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: -1px;
  border-radius: var(--radius);
}
.doc-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.doc-name { font-weight: 500; }
.doc-actions { display: flex; gap: 8px; margin-top: 6px; }
.empty-hint { display: flex; align-items: center; justify-content: center; height: 100%; }

/* Upload Progress */
.upload-progress {
  margin-bottom: 12px;
  padding: 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}
.upload-progress-label {
  display: block;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Chunks Modal */
.chunks-modal { max-height: 60vh; overflow-y: auto; }
.chunk-card { margin-bottom: 8px; }
.chunk-meta { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.chunk-content {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: var(--text-sm);
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
}

/* Create KB Form */
.create-kb-form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; min-width: min(350px, 80vw); }

/* Share Form */
.share-form {
  padding: 20px 24px;
  background: var(--color-surface);
  border-radius: 12px;
  height: 100%;
  box-sizing: border-box;
  color: var(--color-text);
}
.share-add-row { display: flex; gap: 8px; margin-bottom: 20px; }
.share-empty { padding: 20px 0; }
.share-list { max-height: calc(100% - 80px); overflow-y: auto; }
.share-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 8px;
  border-bottom: 1px solid var(--color-border);
  transition: background .15s;
}
.share-row:hover { background: rgba(88, 166, 255, 0.04); }
.share-user-info { display: flex; align-items: center; gap: 10px; }
.share-user-avatar {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  background: var(--color-border);
  border-radius: 50%;
  font-size: 0.9rem;
}
.share-user-name { font-weight: 500; font-size: 0.9rem; }
.share-user-sub { font-size: 0.75rem; color: var(--color-text-muted); }

/* Responsive */
@media (max-width: 767px) {
  .kb-body { flex-direction: column; }
  .kb-list {
    width: 100%;
    max-height: 160px;
    display: flex;
    gap: 8px;
    overflow-x: auto;
    overflow-y: hidden;
    flex-shrink: 0;
    padding-bottom: 4px;
  }
  .kb-list :deep(.n-card) {
    min-width: 160px;
    flex-shrink: 0;
    margin-bottom: 0;
  }
  .kb-docs { flex: 1; overflow-y: auto; }
  .docs-header { flex-direction: column; align-items: flex-start; }
}
</style>
