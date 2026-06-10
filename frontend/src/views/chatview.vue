<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NInput, NButton, NIcon, NTag, NSelect } from 'naive-ui'
import { Send } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, listConversations } from '@/api/chat'
import { listKnowledgeBases } from '@/api/documents'
import type { ChatMessage as ChatMsg, Citation, KnowledgeBase } from '@/types'

const route = useRoute()

const messages = ref<ChatMsg[]>([])
const inputText = ref('')
const isStreaming = ref(false)
const conversationId = ref<string>()
const kbs = ref<KnowledgeBase[]>([])
const selectedKbId = ref('')

onMounted(async () => {
  const id = route.params.id as string | undefined
  if (id) conversationId.value = id
  // Load available KBs
  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    if (kbs.value.length > 0 && !selectedKbId.value) {
      selectedKbId.value = kbs.value[0].id
    }
  } catch { /* noop */ }
})

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return

  inputText.value = ''

  // Add user message
  messages.value.push({
    id: crypto.randomUUID(),
    role: 'user',
    content: text,
    citations: [],
    created_at: new Date().toISOString(),
  })

  // Add placeholder assistant message
  const assistantMsg: ChatMsg = {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    citations: [],
    created_at: new Date().toISOString(),
  }
  messages.value.push(assistantMsg)

  isStreaming.value = true

  try {
    for await (const event of streamChat(text, selectedKbId.value, conversationId.value)) {
      if (event.type === 'token') {
        assistantMsg.content += event.content
      } else if (event.type === 'citation') {
        assistantMsg.citations.push(event.citation)
      } else if (event.type === 'error') {
        assistantMsg.content = `❌ 错误: ${event.message}`
      } else if (event.type === 'done') {
        conversationId.value = event.conversation_id
      }
    }
  } catch (e: any) {
    assistantMsg.content = `❌ 连接失败: ${e.message}`
  } finally {
    isStreaming.value = false
    await nextTick()
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}
</script>

<template>
  <div class="chat-view">
    <div class="chat-header">
      <h2>💬 RAG 对话</h2>
      <NSelect
        v-model:value="selectedKbId"
        :options="kbs.map(k => ({ label: k.name, value: k.id }))"
        placeholder="选择知识库"
        style="width:200px"
        size="small"
      />
      <NTag v-if="kbs.length === 0" type="warning">请先在知识库页面上传文档</NTag>
    </div>

    <!-- Messages -->
    <div class="chat-messages">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🔍</div>
        <h3>开始 RAG 对话</h3>
        <p>选择一个知识库，输入问题，AI 将从你的文档中检索相关内容并生成回答。</p>
      </div>

      <ChatMessage
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
      />
    </div>

    <!-- Input -->
    <div class="chat-input-area">
      <NInput
        v-model:value="inputText"
        type="textarea"
        placeholder="输入你的问题... (Enter 发送, Shift+Enter 换行)"
        :autosize="{ minRows: 1, maxRows: 4 }"
        :disabled="isStreaming"
        @keydown="handleKeydown"
      />
      <NButton
        type="primary"
        :disabled="!inputText.trim() || isStreaming"
        @click="sendMessage"
      >
        <template #icon><NIcon><Send /></NIcon></template>
        发送
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 900px;
  margin: 0 auto;
}
.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-header h2 { font-size: 1.25rem; }
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 0;
}
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--color-text-muted);
}
.empty-icon { font-size: 3rem; margin-bottom: 12px; }
.empty-state h3 { font-size: 1.15rem; color: var(--color-text); margin-bottom: 8px; }
.chat-input-area {
  display: flex;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-input-area :deep(.n-input) { flex: 1; }
</style>
