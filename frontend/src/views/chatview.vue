<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NInput, NButton, NIcon, NTag, NSelect, NCard } from 'naive-ui'
import { Send } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, getConversation } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases } from '@/api/documents'
import type { ChatMessage as ChatMsg } from '@/types'

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
const kbs = ref<any[]>([])
const selectedKbId = ref('')

onMounted(async () => {
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

  const id = route.params.id as string | undefined
  if (id) {
    await loadConversation(id)
  }
})

// Watch route param changes (conversation selected from sidebar)
watch(() => route.params.id, async (id) => {
  const cid = id as string | undefined
  if (cid && cid !== conversationId.value) {
    await loadConversation(cid)
  } else if (!cid) {
    // Navigated to /chat without id — new conversation
    messages.value = []
    conversationId.value = undefined
    isReadonly.value = false
  }
})

// Listen for reset-chat event from sidebar
onMounted(() => {
  window.addEventListener('erag:reset-chat', () => {
    isReadonly.value = false
    conversationId.value = undefined
    messages.value = []
  })
})

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
  } catch {
    messages.value = []
    conversationId.value = undefined
    router.replace('/chat')
  }
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return
  inputText.value = ''

  messages.value.push({ id: crypto.randomUUID(), role: 'user', content: text, citations: [], created_at: new Date().toISOString() })

  const assistantMsg: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], created_at: new Date().toISOString() }
  messages.value.push(assistantMsg)
  const proxyMsg = messages.value[messages.value.length - 1]
  const aid = assistantMsg.id
  await nextTick()
  isStreaming.value = true
  await nextTick()

  try {
    let streamedText = ''
    for await (const event of streamChat(text, selectedKbId.value, conversationId.value)) {
      if (event.type === 'token') {
        streamedText += event.content
        const el = document.getElementById('stream-' + aid)
        if (el) el.textContent = streamedText + '▌'
      } else if (event.type === 'citation') {
        proxyMsg.citations.push(event.citation)
      } else if (event.type === 'error') {
        streamedText = '❌ 错误: ' + event.message
        break
      } else if (event.type === 'done') {
        proxyMsg.content = streamedText
        ;(proxyMsg as any)._ttft = event.ttft_ms || 0
        ;(proxyMsg as any)._retrieval = event.retrieval_ms || 0
        ;(proxyMsg as any)._llm = event.llm_ms || 0
        conversationId.value = event.conversation_id
        router.replace(`/chat/${event.conversation_id}`)
        // Notify sidebar to refresh conversation list
        window.dispatchEvent(new CustomEvent('erag:conversation-updated'))
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

const isComposing = ref(false)
function handleKeydown(e: KeyboardEvent) {
  if (isComposing.value) return
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
}
</script>

<template>
  <div class="chat-view">
    <div class="chat-header">
      <h2>💬 RAG 对话</h2>
      <NTag v-if="isReadonly" type="info">📖 只读模式 — 查看用户对话</NTag>
      <NSelect
        v-if="!isReadonly"
        v-model:value="selectedKbId"
        :options="kbs.map((k: any) => ({ label: k.name, value: k.id }))"
        placeholder="选择知识库"
        style="width:180px"
        size="small"
      />
    </div>

    <div class="chat-messages">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🔍</div>
        <h3>开始 RAG 对话</h3>
        <p>选择一个知识库，输入问题开始对话</p>
      </div>
      <ChatMessage
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
      />
    </div>

    <div v-if="!isReadonly" class="chat-input-area">
      <NInput
        v-model:value="inputText"
        type="textarea"
        placeholder="输入问题... (Enter 发送)"
        :autosize="{ minRows: 1, maxRows: 4 }"
        :disabled="isStreaming"
        @keydown="handleKeydown"
        @compositionstart="isComposing = true"
        @compositionend="isComposing = false"
      />
      <NButton type="primary" :disabled="!inputText.trim() || isStreaming" @click="sendMessage">
        <template #icon><NIcon><Send /></NIcon></template>
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0 12px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-header h2 {
  font-size: 1.15rem;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 0;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-muted);
}
.empty-icon {
  font-size: 3rem;
  margin-bottom: 12px;
}
.empty-state h3 {
  font-size: 1.15rem;
  color: var(--color-text);
  margin-bottom: 8px;
}

.chat-input-area {
  display: flex;
  gap: 8px;
  padding: 12px 0;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-input-area :deep(.n-input) {
  flex: 1;
}
</style>
