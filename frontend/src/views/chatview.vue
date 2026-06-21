<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NInput, NButton, NIcon, NTag, NSelect, NCard, NPopconfirm, NEmpty } from 'naive-ui'
import { Send, Add, Trash } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, listConversations, getConversation, deleteConversation, authHeaders } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases } from '@/api/documents'
import type { ChatMessage as ChatMsg } from '@/types'

interface ConvItem { id: string; title: string; updated_at: string }

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const isReadonly = ref(false)
const conversationOwnerId = ref<string>()
const viewUserId = computed(() => route.query.view_user as string | undefined)

// Force readonly if viewing another user's conversation (even for admin)
function checkReadonly(convUserId?: string | null) {
  if (convUserId && convUserId !== auth.user?.id) {
    isReadonly.value = true
    conversationOwnerId.value = convUserId
  } else {
    isReadonly.value = false
    conversationOwnerId.value = undefined
  }
}

const messages = ref<ChatMsg[]>([])
const inputText = ref('')
const isStreaming = ref(false)
const conversationId = ref<string>()
const conversations = ref<ConvItem[]>([])
const kbs = ref<any[]>([])
const selectedKbId = ref('')

onMounted(async () => {
  // Reset readonly state on every mount
  isReadonly.value = false

  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    if (kbs.value.length > 0 && !selectedKbId.value) selectedKbId.value = kbs.value[0].id
  } catch { /* noop */ }

  // If URL has view_user param, force readonly from start
  if (viewUserId.value && viewUserId.value !== auth.user?.id) {
    isReadonly.value = true
  }

  await loadConversations()

  const id = route.params.id as string | undefined
  if (id) {
    await loadConversation(id)
  } else if (!isReadonly.value && conversations.value.length > 0) {
    await loadConversation(conversations.value[0].id)
  }
})

// Listen for reset-chat event from sidebar
onMounted(() => {
  window.addEventListener('erag:reset-chat', () => {
    isReadonly.value = false
    conversationId.value = undefined
    messages.value = []
    loadConversations()
  })
})

async function loadConversations() {
  try {
    // Always use current user's ID unless explicitly in readonly mode
    const uid = (isReadonly.value && viewUserId.value) ? viewUserId.value : (auth.user?.id || '')
    const url = `/api/conversations?user_id=${uid}`
    const r = await fetch(url, { headers: authHeaders() })
    if (!r.ok) throw new Error('Failed')
    conversations.value = await r.json()
  } catch { conversations.value = [] }
}

async function loadConversation(id: string) {
  try {
    const conv = await getConversation(id)
    messages.value = conv.messages || []
    conversationId.value = id
    checkReadonly((conv as any).user_id)
    if (isReadonly.value) {
      router.replace({ path: `/chat/${id}`, query: { view_user: (conv as any).user_id } })
    } else {
      router.replace(`/chat/${id}`)
    }
  } catch { newConversation() }
}

function newConversation() {
  messages.value = []
  conversationId.value = undefined
  isReadonly.value = false
  router.replace({ path: '/chat' })
}

async function handleDelete(id: string) {
  try {
    await deleteConversation(id)
    if (conversationId.value === id) newConversation()
    await loadConversations()
  } catch { /* noop */ }
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return
  inputText.value = ''

  messages.value.push({ id: crypto.randomUUID(), role: 'user', content: text, citations: [], created_at: new Date().toISOString() })

  const assistantMsg: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], created_at: new Date().toISOString() }
  messages.value.push(assistantMsg)
  // 通过 reactive 数组索引获取 Proxy 引用 — 后续所有修改必须走 Proxy，Vue 才能追踪到
  const proxyMsg = messages.value[messages.value.length - 1]
  const aid = assistantMsg.id
  await nextTick()
  isStreaming.value = true
  await nextTick()  // 等 ChatMessage 的 v-if 分支渲染完毕，stream- span 进入 DOM

  try {
    let streamedText = ''
    for await (const event of streamChat(text, selectedKbId.value, conversationId.value)) {
      if (event.type === 'token') {
        streamedText += event.content
        const el = document.getElementById('stream-' + aid)
        if (el) el.textContent = streamedText + '▌'
      } else if (event.type === 'citation') { proxyMsg.citations.push(event.citation) }
      else if (event.type === 'error') { streamedText = '❌ 错误: ' + event.message; break }
      else if (event.type === 'done') {
        proxyMsg.content = streamedText
        ;(proxyMsg as any)._ttft = event.ttft_ms || 0
        ;(proxyMsg as any)._retrieval = event.retrieval_ms || 0
        ;(proxyMsg as any)._llm = event.llm_ms || 0
        conversationId.value = event.conversation_id
        router.replace(`/chat/${event.conversation_id}`)
        await loadConversations()
      }
    }
    proxyMsg.content = streamedText
  } catch (e: any) {
    proxyMsg.content = `❌ 连接失败: ${e.message}`
  } finally {
    isStreaming.value = false
    await nextTick()
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
}
</script>

<template>
  <div class="chat-layout">
    <!-- Conversation Sidebar -->
    <div class="conv-sidebar">
      <div class="conv-header">
        <span class="conv-title">对话历史</span>
        <NButton v-if="!isReadonly" size="tiny" @click="newConversation">
          <template #icon><NIcon><Add /></NIcon></template>
        </NButton>
      </div>
      <div class="conv-list">
        <NEmpty v-if="conversations.length === 0" description="暂无对话" style="padding:20px" />
        <div
          v-for="c in conversations" :key="c.id"
          :class="['conv-item', { active: c.id === conversationId }]"
          @click="loadConversation(c.id)"
        >
          <div class="conv-item-text">
            <span class="conv-name line-clamp-1">{{ c.title }}</span>
            <span class="conv-time">{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' }) }}</span>
          </div>
          <NPopconfirm v-if="!isReadonly" @positive-click="handleDelete(c.id)">
            <template #trigger>
              <NButton text size="tiny" type="error" @click.stop>
                <NIcon size="14"><Trash /></NIcon>
              </NButton>
            </template>
            删除此对话？
          </NPopconfirm>
        </div>
      </div>
    </div>

    <!-- Chat Area -->
    <div class="chat-main">
      <div class="chat-header">
        <h2>💬 RAG 对话</h2>
        <NTag v-if="isReadonly" type="info">📖 只读模式 — 查看用户对话</NTag>
        <NSelect v-if="!isReadonly"
          v-model:value="selectedKbId"
          :options="kbs.map(k => ({ label: k.name, value: k.id }))"
          placeholder="选择知识库" style="width:180px" size="small"
        />
      </div>

      <div class="chat-messages">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">🔍</div>
          <h3>开始 RAG 对话</h3>
          <p>选择一个知识库，输入问题开始对话</p>
        </div>
        <ChatMessage
          v-for="msg in messages" :key="msg.id" :message="msg"
          :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
        />
      </div>

      <div class="chat-input-area" v-if="!isReadonly">
        <NInput
          v-model:value="inputText" type="textarea"
          placeholder="输入问题... (Enter 发送)"
          :autosize="{ minRows: 1, maxRows: 4 }" :disabled="isStreaming"
          @keydown="handleKeydown"
        />
        <NButton type="primary" :disabled="!inputText.trim() || isStreaming" @click="sendMessage">
          <template #icon><NIcon><Send /></NIcon></template>
        </NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-layout { display: flex; height: 100%; gap: 0; }
.conv-sidebar {
  width: 220px; border-right: 1px solid var(--color-border);
  display: flex; flex-direction: column; flex-shrink: 0; overflow: hidden;
}
.conv-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 12px 8px;
}
.conv-title { font-weight: 600; font-size: 0.95rem; }
.conv-list { flex: 1; overflow-y: auto; }
.conv-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--color-border);
  transition: background .15s;
}
.conv-item:hover { background: rgba(88,166,255,0.06); }
.conv-item.active { background: rgba(88,166,255,0.1); }
.conv-item-text { flex: 1; min-width: 0; margin-right: 4px; }
.conv-name { display: block; font-size: 0.85rem; }
.conv-time { font-size: 0.7rem; color: var(--color-text-muted); }
.chat-main {
  flex: 1; display: flex; flex-direction: column; min-width: 0;
  padding: 0 24px;
}
.chat-header {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 0 12px; border-bottom: 1px solid var(--color-border); flex-shrink: 0;
}
.chat-header h2 { font-size: 1.15rem; }
.chat-messages { flex: 1; overflow-y: auto; padding: 12px 0; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--color-text-muted); }
.empty-icon { font-size: 3rem; margin-bottom: 12px; }
.empty-state h3 { font-size: 1.15rem; color: var(--color-text); margin-bottom: 8px; }
.chat-input-area {
  display: flex; gap: 8px; padding: 12px 0;
  border-top: 1px solid var(--color-border); flex-shrink: 0;
}
.chat-input-area :deep(.n-input) { flex: 1; }
</style>
