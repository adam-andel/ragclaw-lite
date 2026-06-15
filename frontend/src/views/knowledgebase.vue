<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NButton, NModal, NInput, NCard, NSpace, NTag, NEmpty, NPopconfirm, NIcon, NSelect } from 'naive-ui'
import { Add, Trash, CloudUpload, People } from '@vicons/ionicons5'
import { listKnowledgeBases, createKnowledgeBase, deleteKnowledgeBase, uploadDocument, listDocuments, getDocumentChunks, deleteDocument } from '@/api/documents'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase, DocumentItem, ChunkItem } from '@/types'

const kbs = ref<KnowledgeBase[]>([])
const selectedKbId = ref<string>('')
const documents = ref<DocumentItem[]>([])
const chunks = ref<ChunkItem[]>([])
const showChunksFor = ref(false)
const loadingDocs = ref(false)

const showCreateKb = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const creating = ref(false)
const auth = useAuthStore()

onMounted(() => loadKBs())

async function loadKBs() {
  try { const res = await listKnowledgeBases(); kbs.value = res.data } catch { /* noop */ }
}

async function handleCreateKb() {
  if (!newKbName.value.trim()) return
  creating.value = true
  try {
    await createKnowledgeBase({ name: newKbName.value, description: newKbDesc.value })
    await loadKBs()
    showCreateKb.value = false
    newKbName.value = ''; newKbDesc.value = ''
  } catch (e: any) { console.error(e.message) }
  finally { creating.value = false }
}

function triggerFileUpload() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.pdf,.docx,.md,.txt'
  input.onchange = (e: Event) => {
    const file = (e.target as HTMLInputElement).files?.[0]
    if (file) handleUpload(file)
  }
  input.click()
}

async function handleDeleteKb(id: string) {
  try {
    await deleteKnowledgeBase(id)
    if (selectedKbId.value === id) selectedKbId.value = ''
    await loadKBs()
  } catch (e: any) { console.error(e.message) }
}

async function selectKb(id: string) { selectedKbId.value = id; await loadDocuments() }

async function loadDocuments() {
  if (!selectedKbId.value) return
  loadingDocs.value = true
  try { const res = await listDocuments(selectedKbId.value); documents.value = res.data } finally { loadingDocs.value = false }
}

async function handleUpload(file: File) {
  if (!selectedKbId.value) { console.warn('请先选择一个知识库'); return }
  try {
    await uploadDocument(selectedKbId.value, file)
    await new Promise((r) => setTimeout(r, 2000))
    await loadDocuments()
  } catch (e: any) { console.error(e.message) }
}

async function handleDeleteDoc(id: string) {
  try { await deleteDocument(id); await loadDocuments() } catch (e: any) { console.error(e.message) }
}

async function showChunks(docId: string) {
  try { const res = await getDocumentChunks(docId); chunks.value = res.data; showChunksFor.value = true } catch { console.error('获取分块失败') }
}

// ---- Sharing ----
const showShare = ref(false)
const shareKbId = ref('')
const shareUsers = ref<any[]>([])
const shareAddUser = ref('')
const allUsers = ref<any[]>([])
const allUserOptions = ref<{label:string;value:string}[]>([])

async function openShare(kbId: string) {
  shareKbId.value = kbId
  try { const r = await client.get(`/kb/${kbId}/users`); shareUsers.value = r.data } catch { shareUsers.value = [] }
  // Load all users for the dropdown
  try { const r = await client.get('/users'); allUsers.value = r.data } catch { allUsers.value = [] }
  allUserOptions.value = allUsers.value
    .filter((u: any) => !shareUsers.value.some((s: any) => s.id === u.id))
    .map((u: any) => ({ label: `${u.display_name || u.username} (${u.username})`, value: u.id }))
  showShare.value = true
}
async function addKbUser(uid: string) {
  if (!uid) return
  try {
    await client.post(`/kb/${shareKbId.value}/users/${uid}`)
    const r = await client.get(`/kb/${shareKbId.value}/users`); shareUsers.value = r.data
    // Refresh options
    allUserOptions.value = allUsers.value
      .filter((u: any) => !shareUsers.value.some((s: any) => s.id === u.id))
      .map((u: any) => ({ label: `${u.display_name || u.username} (${u.username})`, value: u.id }))
    shareAddUser.value = ''
  } catch (e: any) { console.error(e.message) }
}
async function removeKbUser(uid: string) {
  try {
    await client.delete(`/kb/${shareKbId.value}/users/${uid}`)
    const r = await client.get(`/kb/${shareKbId.value}/users`); shareUsers.value = r.data
  } catch { /* noop */ }
}

const selectedKb = () => kbs.value.find((k) => k.id === selectedKbId.value)

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
      <div class="kb-list">
        <NEmpty v-if="kbs.length === 0" description="暂无知识库" />
        <NCard v-for="kb in kbs" :key="kb.id" :class="['kb-card', { active: kb.id === selectedKbId }]" size="small" @click="selectKb(kb.id)">
          <div class="kb-card-header">
            <strong>{{ kb.name }}</strong>
          </div>
        </NCard>
      </div>

      <div class="kb-docs">
        <div v-if="!selectedKbId" class="empty-hint"><NEmpty description="选择一个知识库查看文档" /></div>
        <template v-else>
          <div class="docs-header">
            <h3>{{ selectedKb()?.name }} · 文档</h3>
            <NSpace>
              <NButton v-if="auth.isStaff" size="small" @click="openShare(selectedKbId)">
                <template #icon><NIcon><People /></NIcon></template>
                共享
              </NButton>
              <NButton dashed size="small" @click="triggerFileUpload">
                <template #icon><NIcon><CloudUpload /></NIcon></template>
                上传文档
              </NButton>
              <NPopconfirm @positive-click="handleDeleteKb(selectedKbId)">
                <template #trigger><NButton size="small" type="error">
                  <template #icon><NIcon><Trash /></NIcon></template>
                  删除
                </NButton></template>
                确定删除此知识库及其所有文档？
              </NPopconfirm>
            </NSpace>
          </div>

          <NEmpty v-if="documents.length === 0" description="暂无文档" />
          <NCard v-for="doc in documents" :key="doc.id" size="small" class="doc-card">
            <div class="doc-info">
              <span class="doc-name">📄 {{ doc.filename }}</span>
              <NSpace size="small">
                <NTag :type="statusColors[doc.status] as any" size="small">{{ statusLabels[doc.status] || doc.status }}</NTag>
                <NTag size="small">{{ formatSize(doc.file_size) }}</NTag>
                <NTag size="small" v-if="doc.chunk_count > 0">{{ doc.chunk_count }} 分块</NTag>
              </NSpace>
            </div>
            <div class="doc-actions">
              <NButton text size="tiny" @click="showChunks(doc.id)">查看分块</NButton>
              <NPopconfirm @positive-click="handleDeleteDoc(doc.id)">
                <template #trigger><NButton text size="tiny" type="error">删除</NButton></template>
                确定删除？
              </NPopconfirm>
            </div>
          </NCard>
        </template>
      </div>
    </div>

    <NModal v-model:show="showChunksFor" title="分块预览" style="max-width:800px">
      <div class="chunks-modal">
        <NCard v-for="c in chunks" :key="c.id" size="small" class="chunk-card">
          <div class="chunk-meta">
            <NTag size="tiny">#{{ c.chunk_index }}</NTag>
            <NTag size="tiny" v-if="c.heading">{{ c.heading }}</NTag>
            <span>{{ c.token_count }} tokens</span>
          </div>
          <p>{{ c.content.slice(0, 300) }}...</p>
        </NCard>
        <NButton @click="showChunksFor = false">关闭</NButton>
      </div>
    </NModal>

    <NModal v-model:show="showCreateKb" title="新建知识库">
      <div class="create-kb-form">
        <NInput v-model:value="newKbName" placeholder="知识库名称" />
        <NInput v-model:value="newKbDesc" placeholder="描述（可选）" type="textarea" :autosize="{ minRows: 2 }" />
        <NButton type="primary" :loading="creating" @click="handleCreateKb" block>创建</NButton>
      </div>
    </NModal>

    <NModal v-model:show="showShare" title="共享管理" style="width:70vw; max-width:1000px; height:70vh; max-height:800px" :title-style="{fontSize:'1.25rem',fontWeight:'bold'}">
      <div class="share-form">
        <div class="share-add-row">
          <NSelect
            v-model:value="shareAddUser"
            :options="allUserOptions"
            placeholder="搜索用户..."
            filterable clearable size="large"
            style="flex:1"
          />
          <NButton type="primary" size="large" :disabled="!shareAddUser" @click="addKbUser(shareAddUser)">
            <template #icon><NIcon><Add /></NIcon></template>
            添加
          </NButton>
        </div>

        <div v-if="shareUsers.length === 0" class="share-empty">
          <NEmpty description="暂无共享用户" />
        </div>

        <div class="share-list">
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
.kb-card { margin-bottom: 8px; cursor: pointer; transition: border-color .2s; }
.kb-card.active { border-color: var(--color-primary); }
.kb-card-header { display: flex; justify-content: space-between; align-items: center; }
.docs-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.doc-card { margin-bottom: 8px; }
.doc-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.doc-name { font-weight: 500; }
.doc-actions { display: flex; gap: 8px; margin-top: 6px; }
.empty-hint { display: flex; align-items: center; justify-content: center; height: 100%; }
.chunks-modal { max-height: 60vh; overflow-y: auto; }
.chunk-card { margin-bottom: 8px; }
.chunk-meta { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.create-kb-form { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; min-width: 350px; }
.share-form { padding: 20px 24px; background: #fff; border-radius: 12px; height: 100%; box-sizing: border-box; }
.share-add-row { display: flex; gap: 8px; margin-bottom: 20px; }
.share-empty { padding: 20px 0; }
.share-list { max-height: calc(100% - 80px); overflow-y: auto; }
.share-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 8px; border-bottom: 1px solid var(--color-border); transition: background .15s; }
.share-row:hover { background: rgba(88,166,255,0.04); }
.share-user-info { display: flex; align-items: center; gap: 10px; }
.share-user-avatar { width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; background: var(--color-border); border-radius: 50%; font-size: 0.9rem; }
.share-user-name { font-weight: 500; font-size: 0.9rem; }
.share-user-sub { font-size: 0.75rem; color: var(--color-text-muted); }
</style>
