<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NInput, NButton, NIcon, NTag, NCard, NEmpty, NModal, NSpace, useMessage } from 'naive-ui'
import { Send, StopCircle, Chatbubbles, List, Add, ChevronDown } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, getConversation, listConversations } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases } from '@/api/documents'
import { renderStreamingHtml } from '@/utils/think'
import type { ChatMessage as ChatMsg } from '@/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const nmessage = useMessage()

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
const queuePosition = ref<number | null>(null)
let abortCtl: AbortController | null = null
const conversationId = ref<string>()
const messagesContainer = ref<HTMLElement>()
const isPinnedToBottom = ref(true)

async function scrollToBottom() {
  await nextTick()
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = messagesContainer.value
  if (!el) return
  const threshold = 60
  isPinnedToBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

function scrollToBottomAndPin() {
  isPinnedToBottom.value = true
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

const showScrollBottomBtn = computed(() => !isPinnedToBottom.value && messages.value.length > 0)
const kbs = ref<any[]>([])
const selectedKbId = ref('')
const conversations = ref<any[]>([])
const emptyMode = ref<'conv' | 'kb' | ''>('')
const showMoreConv = ref(false)
const showMoreKb = ref(false)
const kbSearchText = ref('')
const convKbMap = ref<Record<string, string>>({})

const convPreview = computed(() => conversations.value.slice(0, 3))
const convHasMore = computed(() => conversations.value.length > 3)
const kbPreview = computed(() => kbs.value.slice(0, 3))
const kbHasMore = computed(() => kbs.value.length > 3)

const showPicker = computed(() => emptyMode.value !== '' && messages.value.length === 0 && !conversationId.value)

const selectedKb = computed(() => kbs.value.find((k: any) => k.id === selectedKbId.value))
const currentKbName = computed(() => selectedKb.value?.name || '选择知识库')
const filteredKbs = computed(() =>
  kbs.value.filter((kb: any) =>
    !kbSearchText.value || kb.name.toLowerCase().includes(kbSearchText.value.toLowerCase())
  )
)

function selectAndClose(convId: string) {
  emptyMode.value = ''
  showMoreConv.value = false
  router.push(`/chat/${convId}`)
}

async function loadConversations() {
  try {
    conversations.value = await listConversations()
  } catch { conversations.value = [] }
}

onMounted(async () => {
  isReadonly.value = false

  // Load persisted conversation→KB mapping
  try {
    const stored = localStorage.getItem('erag:conv-kb-map')
    if (stored) convKbMap.value = JSON.parse(stored)
  } catch { /* ignore */ }

  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    const kbFromQuery = route.query.kb as string | undefined
    if (kbFromQuery && kbs.value.find(k => k.id === kbFromQuery)) {
      selectedKbId.value = kbFromQuery
    } else if (kbs.value.length > 0 && !selectedKbId.value) {
      selectedKbId.value = kbs.value[0].id
    }
  } catch { /* noop */ }

  await loadConversations()

  // If URL has view_user param, force readonly from start
  if (viewUserId.value && viewUserId.value !== auth.user?.id) {
    isReadonly.value = true
  }

  const id = route.params.id as string | undefined
  if (id) {
    await loadConversation(id)
  }
})

// Persist KB selection when conversation has messages
watch(selectedKbId, (newKbId) => {
  if (newKbId && conversationId.value && messages.value.length > 0) {
    convKbMap.value[conversationId.value] = newKbId
    localStorage.setItem('erag:conv-kb-map', JSON.stringify(convKbMap.value))
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
    emptyMode.value = 'conv'
    await loadConversations()
  }
})

async function loadConversation(id: string) {
  try {
    const conv = await getConversation(id)
    messages.value = conv.messages || []
    conversationId.value = id
    // Restore the KB that was used with this conversation
    const savedKbId = convKbMap.value[id]
    if (savedKbId && kbs.value.find(k => k.id === savedKbId)) {
      selectedKbId.value = savedKbId
    }
    checkReadonly((conv as any).user_id)
    if (isReadonly.value) {
      router.replace({ path: `/chat/${id}`, query: { view_user: (conv as any).user_id } })
    } else {
      router.replace(`/chat/${id}`)
    }
    isPinnedToBottom.value = true
    await scrollToBottom()
  } catch {
    messages.value = []
    conversationId.value = undefined
    router.replace('/chat')
  }
}

// Listen for reset-chat event from sidebar
onMounted(() => {
  window.addEventListener('erag:reset-chat', () => {
    isReadonly.value = false
    conversationId.value = undefined
    messages.value = []
    emptyMode.value = 'conv'
    loadConversations()
  })
})

async function doStream(query: string, proxyMsg: ChatMsg, userMsgId: string) {
  const aid = proxyMsg.id
  let streamedText = ''
  queuePosition.value = null
  abortCtl = new AbortController()
  try {
    for await (const event of streamChat(query, selectedKbId.value, conversationId.value, undefined, abortCtl.signal)) {
      if (event.type === 'queue') {
        queuePosition.value = event.position
      } else if (event.type === 'token') {
        streamedText += event.content
        const el = document.getElementById('stream-' + aid)
        if (el) {
          let html = renderStreamingHtml(streamedText)
          const cursor = '<span class="cursor-blink">▌</span>'
          // Inject the cursor inside the last paragraph so it stays inline
          // with the streaming text instead of dropping to a new line.
          if (html.endsWith('</p>\n')) {
            html = html.slice(0, -5) + cursor + '</p>\n'
          } else if (html.endsWith('</p>')) {
            html = html.slice(0, -4) + cursor + '</p>'
          } else {
            html += cursor
          }
          el.innerHTML = html
          if (isPinnedToBottom.value) {
            const container = messagesContainer.value
            if (container) container.scrollTop = container.scrollHeight
          }
        }
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
        // Persist KB for newly created conversation
        if (!convKbMap.value[event.conversation_id]) {
          convKbMap.value[event.conversation_id] = selectedKbId.value
          localStorage.setItem('erag:conv-kb-map', JSON.stringify(convKbMap.value))
        }
        router.replace(`/chat/${event.conversation_id}`)
        window.dispatchEvent(new CustomEvent('erag:conversation-updated'))
      }
    }
    proxyMsg.content = streamedText
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      // Remove failed user + assistant messages and restore input
      messages.value = messages.value.filter(m => m.id !== userMsgId && m.id !== proxyMsg.id)
      inputText.value = query
      nmessage.error(`发送失败: ${e.message}，已恢复输入`)
    }
  } finally {
    isStreaming.value = false
    queuePosition.value = null
    abortCtl = null
    await nextTick()
    // After switching from streaming to final render (citations, badges, etc.),
    // re-pin to bottom if the user hasn't scrolled away.
    if (isPinnedToBottom.value) {
      const el = messagesContainer.value
      if (el) el.scrollTop = el.scrollHeight
    }
  }
}

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || isStreaming.value) return

  const userMsg: ChatMsg = { id: crypto.randomUUID(), role: 'user', content: text, citations: [], created_at: new Date().toISOString() }
  messages.value.push(userMsg)
  inputText.value = ''

  const assistantMsg: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], created_at: new Date().toISOString() }
  messages.value.push(assistantMsg)
  const proxyMsg = messages.value[messages.value.length - 1]
  isPinnedToBottom.value = true
  await scrollToBottom()
  isStreaming.value = true
  await nextTick()
  doStream(text, proxyMsg, userMsg.id)
}

async function regenerateAnswer(assistantMsgId: string) {
  if (isStreaming.value) return
  const idx = messages.value.findIndex(m => m.id === assistantMsgId)
  if (idx < 1) return
  const userMsg = messages.value[idx - 1]
  if (userMsg.role !== 'user') return

  // replace old assistant message with fresh placeholder
  const newAssistant: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], created_at: new Date().toISOString() }
  messages.value.splice(idx, 1, newAssistant)
  const proxyMsg = messages.value[idx]
  isPinnedToBottom.value = true
  await scrollToBottom()
  isStreaming.value = true
  await nextTick()
  doStream(userMsg.content, proxyMsg, userMsg.id)
}

function stopStream() {
  abortCtl?.abort()
  isStreaming.value = false
  queuePosition.value = null
  abortCtl = null
}

function cancelQueue() {
  abortCtl?.abort()
  isStreaming.value = false
  queuePosition.value = null
  abortCtl = null
}

function newConversation() {
  conversationId.value = undefined
  messages.value = []
  isReadonly.value = false
  emptyMode.value = 'kb'
  loadConversations()
  router.replace('/chat')
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
      <div class="kb-header-title">
        <NIcon size="22" color="var(--color-primary)"><Chatbubbles /></NIcon>
        <h2>RAG 对话</h2>
      </div>
      <div class="chat-header-right">
        <NTag v-if="isReadonly" type="info">📖 只读模式 — 查看用户对话</NTag>
        <template v-if="!isReadonly">
          <span class="kb-select-label">当前知识库</span>
          <NButton size="small" @click="showMoreKb = true" class="kb-trigger-btn">
            {{ currentKbName }}
          </NButton>
        </template>
        <NButton size="small" @click="showMoreConv = true">
          <template #icon><NIcon size="16"><List /></NIcon></template>
          对话历史
        </NButton>
        <NButton v-if="!isReadonly" size="small" type="primary" @click="newConversation">
          <template #icon><NIcon size="16"><Add /></NIcon></template>
          新建对话
        </NButton>
      </div>
    </div>

    <div class="chat-messages" ref="messagesContainer" @scroll="onScroll" role="log" aria-live="polite" aria-label="对话消息">
      <!-- Centered panel: conversation list preview -->
      <div v-if="showPicker && emptyMode === 'conv'" class="center-panel">
        <div class="center-panel-box">
          <div class="empty-icon">💬</div>
          <h3>选择一个对话</h3>
          <div class="center-panel-list">
            <NCard v-for="c in convPreview" :key="c.id" size="small" class="conv-pick-card"
              role="button" tabindex="0"
              @click="selectAndClose(c.id)"
              @keydown.enter.prevent="selectAndClose(c.id)"
              @keydown.space.prevent="selectAndClose(c.id)"
            >
              <div class="conv-pick-name">{{ c.title || '新对话' }}</div>
              <div class="conv-pick-meta">
                <span>{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) }}</span>
                <NTag v-if="c.message_count" size="tiny">{{ c.message_count }} 条消息</NTag>
              </div>
            </NCard>
          </div>
          <NButton v-if="convHasMore" text size="small" type="primary" @click="showMoreConv = true">
            更多对话 ({{ conversations.length }})
          </NButton>
          <NEmpty v-if="conversations.length === 0" description="暂无对话记录" style="padding:8px 0" />
          <div class="center-panel-actions" style="justify-content:center">
            <span class="fallback-hint">或者<NButton text type="primary" @click="emptyMode = 'kb'" style="padding:0 2px;height:auto;vertical-align:baseline;font-size:inherit">新建对话</NButton></span>
          </div>
        </div>
      </div>

      <!-- Centered panel: KB list preview -->
      <div v-else-if="showPicker && emptyMode === 'kb'" class="center-panel">
        <div class="center-panel-box">
          <div class="empty-icon">🧠</div>
          <h3>新建对话 — 选择知识库</h3>
          <div class="center-panel-list">
            <NCard v-for="kb in kbPreview" :key="kb.id" size="small" class="kb-pick-card"
              :class="{ active: kb.id === selectedKbId }"
              role="button" tabindex="0"
              @click="selectedKbId = kb.id"
              @keydown.enter.prevent="selectedKbId = kb.id"
              @keydown.space.prevent="selectedKbId = kb.id"
            >
              <strong>{{ kb.name }}</strong>
              <span v-if="kb.description" class="kb-pick-desc">{{ kb.description }}</span>
              <span class="kb-pick-meta">{{ kb.doc_count }} 文档 · {{ kb.vector_count }} 向量</span>
            </NCard>
          </div>
          <NButton v-if="kbHasMore" text size="small" type="primary" @click="showMoreKb = true">
            更多知识库 ({{ kbs.length }})
          </NButton>
          <div v-if="kbs.length === 0" class="picker-empty">
            <NEmpty description="还没有知识库" style="padding:8px 0" />
            <NButton type="primary" dashed size="small" @click="router.push('/knowledge')">
              前往创建知识库
            </NButton>
          </div>
          <div class="center-panel-actions">
            <div class="picker-footer-hint">
              已选：<strong>{{ selectedKbId ? (kbs.find(k => k.id === selectedKbId)?.name ?? '...') : '未选择' }}</strong>
            </div>
            <NSpace>
              <NButton v-if="conversations.length > 0" @click="emptyMode = 'conv'">← 返回</NButton>
              <NButton type="primary" @click="emptyMode = ''" :disabled="!selectedKbId">开始对话</NButton>
            </NSpace>
          </div>
        </div>
      </div>

      <!-- KB selected, ready to chat -->
      <div v-else-if="messages.length === 0 && !conversationId && selectedKbId" class="empty-state">
        <template v-if="selectedKb">
          <div class="empty-icon">🧠</div>
          <h3>{{ selectedKb.name }}</h3>
          <p v-if="selectedKb.description">{{ selectedKb.description }}</p>
          <p v-else>在下方输入问题开始对话</p>
          <div class="center-panel-actions" style="margin-top:12px; gap:4px; justify-content:center">
            <NButton size="small" @click="emptyMode = 'kb'">更换知识库</NButton>
          </div>
        </template>
      </div>

      <!-- Fallback empty: no conversation, picker not yet opened -->
      <div v-else-if="messages.length === 0 && !conversationId" class="empty-state">
        <div class="empty-icon">💬</div>
        <h3>开始对话</h3>
        <p>选择一个已有对话继续，或开始新的对话</p>
        <NButton type="primary" size="small" @click="emptyMode = 'conv'" style="margin-top:8px">
          选择对话
        </NButton>
        <p class="fallback-hint">或者<NButton text size="tiny" type="primary" @click="emptyMode = 'kb'" style="padding:0 2px;height:auto;vertical-align:baseline">新建对话</NButton></p>
      </div>

      <!-- Edge case: conversation loaded but no messages -->
      <div v-else-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🔍</div>
        <h3>对话为空</h3>
        <p>输入问题开始对话</p>
      </div>
      <ChatMessage
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
        :queue-position="queuePosition"
        @regenerate="regenerateAnswer"
      />
    </div>

    <Transition name="scroll-btn">
      <button
        v-if="showScrollBottomBtn"
        class="scroll-bottom-btn"
        :class="{ streaming: isStreaming }"
        @click="scrollToBottomAndPin"
        title="回到底部"
        aria-label="回到底部"
      >
        <NIcon size="20"><ChevronDown /></NIcon>
      </button>
    </Transition>

    <!-- Modal: full conversation list -->
    <NModal v-model:show="showMoreConv" preset="card" title="所有对话"
      style="width: 90vw; max-width: 520px"
    >
      <div class="picker-scroll">
        <NCard v-for="c in conversations" :key="c.id" size="small" class="conv-pick-card"
          role="button" tabindex="0"
          @click="selectAndClose(c.id)"
          @keydown.enter.prevent="selectAndClose(c.id)"
          @keydown.space.prevent="selectAndClose(c.id)"
        >
          <div class="conv-pick-name">{{ c.title || '新对话' }}</div>
          <div class="conv-pick-meta">
            <span>{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) }}</span>
            <NTag v-if="c.message_count" size="tiny">{{ c.message_count }} 条消息</NTag>
          </div>
        </NCard>
      </div>
    </NModal>

    <!-- Modal: KB picker with search -->
    <NModal v-model:show="showMoreKb" preset="card" title="选择知识库"
      style="width: 90vw; max-width: 520px"
      @after-leave="kbSearchText = ''"
    >
      <NInput v-model:value="kbSearchText" placeholder="搜索知识库名称..." clearable style="margin-bottom:12px" />
      <template v-if="filteredKbs.length > 0">
        <div class="picker-scroll">
          <NCard v-for="kb in filteredKbs" :key="kb.id" size="small" class="kb-pick-card"
            :class="{ active: kb.id === selectedKbId }"
            role="button" tabindex="0"
            @click="selectedKbId = kb.id; showMoreKb = false"
            @keydown.enter.prevent="selectedKbId = kb.id; showMoreKb = false"
            @keydown.space.prevent="selectedKbId = kb.id; showMoreKb = false"
          >
            <strong>{{ kb.name }}</strong>
            <span v-if="kb.description" class="kb-pick-desc">{{ kb.description }}</span>
            <span class="kb-pick-meta">{{ kb.doc_count }} 文档 · {{ kb.vector_count }} 向量</span>
          </NCard>
        </div>
      </template>
      <NEmpty v-else description="没有匹配的知识库" style="padding:16px 0" />
    </NModal>

    <div v-if="!isReadonly" class="chat-input-area">
      <NInput
        v-model:value="inputText"
        type="textarea"
        :placeholder="auth.llmConfigured ? '输入问题... (Enter 发送)' : '请先前往系统设置页面配置API KEY'"
        :autosize="{ minRows: 1, maxRows: 4 }"
        :disabled="isStreaming || !auth.llmConfigured"
        @keydown="handleKeydown"
        @compositionstart="isComposing = true"
        @compositionend="isComposing = false"
      />
      <NButton v-if="queuePosition != null && queuePosition > 0" type="warning" @click="cancelQueue">
        <template #icon><NIcon><StopCircle /></NIcon></template>
        取消排队
      </NButton>
      <NButton v-else-if="isStreaming" type="warning" @click="stopStream">
        <template #icon><NIcon><StopCircle /></NIcon></template>
        停止
      </NButton>
      <NButton v-else type="primary" :disabled="!inputText.trim()" @click="sendMessage">
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
  position: relative;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 14px 20px;
  margin-bottom: 4px;
  background: linear-gradient(135deg, var(--color-primary-soft), transparent);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-header .kb-header-title { display: flex; align-items: center; gap: 10px; }
.chat-header .kb-header-title h2 { font-size: var(--text-xl); font-weight: 700; }
.chat-header-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.kb-select-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-weight: 500;
  white-space: nowrap;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-3) 0;
}

.empty-state {
  text-align: center;
  padding: 60px var(--space-5);
  color: var(--color-text-muted);
}
.empty-icon {
  font-size: 3rem;
  margin-bottom: var(--space-3);
}
.empty-state h3 {
  font-size: var(--text-lg);
  color: var(--color-text);
  margin-bottom: var(--space-2);
}
.empty-state p {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  max-width: 360px;
  margin: 0 auto;
}

/* Centered picker panel (inline, up to 3 items) */
.center-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
}
.center-panel-box {
  text-align: center;
  max-width: 440px;
  width: 100%;
}
.center-panel-box h3 {
  font-size: var(--text-lg);
  margin: 4px 0 8px;
  color: var(--color-text);
}
.center-panel-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0 8px;
  text-align: left;
}
.center-panel-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}

/* Modal scrollable list (shared by "更多对话" and "更多知识库") */
.picker-scroll { max-height: 60vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.conv-pick-card {
  cursor: pointer;
  transition: border-color .2s, box-shadow .2s;
}
.conv-pick-card:hover { border-color: var(--color-primary); box-shadow: var(--shadow-sm); }
.conv-pick-name { font-weight: 600; font-size: var(--text-sm); margin-bottom: 4px; }
.conv-pick-meta { display: flex; align-items: center; gap: 8px; font-size: var(--text-xs); color: var(--color-text-muted); }

.kb-pick-card {
  cursor: pointer;
  transition: border-color .2s, box-shadow .2s;
  border-left: 3px solid transparent;
}
.kb-pick-card:hover { border-color: var(--color-primary); box-shadow: var(--shadow-sm); }
.kb-pick-card.active {
  border-color: var(--color-primary);
  border-left-color: var(--color-primary);
  background: var(--color-primary-soft);
}
.kb-pick-card strong { display: block; font-size: var(--text-sm); margin-bottom: 2px; }
.kb-pick-desc { display: block; font-size: var(--text-xs); color: var(--color-text-muted); margin-bottom: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-meta { font-size: 0.65rem; color: var(--color-text-muted); }
.picker-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 8px 0; }
.picker-footer-hint { font-size: var(--text-xs); color: var(--color-text-muted); }
.picker-footer-hint strong { color: var(--color-text); }
.fallback-hint { margin-top: 8px; font-size: var(--text-base); color: var(--color-text-muted); }
.chat-input-area {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.chat-input-area :deep(.n-input) {
  flex: 1;
}

/* ── Scroll-to-bottom button ── */
.scroll-bottom-btn {
  position: absolute;
  bottom: 84px;
  right: 24px;
  width: 40px;
  height: 40px;
  padding: 0;
  border-radius: 50%;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  z-index: 10;
  transition: color 0.15s, border-color 0.15s;
}
.scroll-bottom-btn:hover {
  color: var(--color-primary);
  border-color: var(--color-primary);
}
/* Spinning ring around the button edge while the answer is still streaming */
.scroll-bottom-btn.streaming::before {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid transparent;
  border-top-color: var(--color-primary);
  border-right-color: var(--color-primary);
  animation: scroll-btn-spin 0.8s linear infinite;
}
@keyframes scroll-btn-spin {
  to { transform: rotate(360deg); }
}

/* Button enter/leave transition */
.scroll-btn-enter-active,
.scroll-btn-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.scroll-btn-enter-from,
.scroll-btn-leave-to {
  opacity: 0;
  transform: translateY(10px);
}

/* ── KB trigger button ── */
.kb-trigger-btn {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Mobile: header wraps gracefully ── */
@media (max-width: 767px) {
  .chat-header {
    flex-wrap: wrap;
    padding: 10px 14px;
    gap: 6px;
  }
  .chat-header .kb-header-title h2 {
    font-size: var(--text-base);
  }
  .chat-header-right {
    flex-wrap: wrap;
    gap: 4px;
  }
  .kb-select-label {
    display: none;
  }
  .kb-trigger-btn {
    max-width: 120px;
  }
  .scroll-bottom-btn {
    right: 14px;
    bottom: 78px;
  }
}
</style>
