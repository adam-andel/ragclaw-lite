<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NInput, NButton, NIcon, NTag, NCard, NEmpty, NModal, NSpace, NPagination, useMessage } from 'naive-ui'
import KbPickerModal from '@/components/kb/KbPickerModal.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { Send, StopCircle, Chatbubbles, List, Add, ChevronDown, Sparkles } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, getConversation, listConversations } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases } from '@/api/documents'
import { listSkills } from '@/api/skills'
import { renderStreamingHtml } from '@/utils/think'
import type { ChatMessage as ChatMsg, Skill } from '@/types'

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
const skills = ref<Skill[]>([])
const selectedSkillId = ref<string | null>(null)
const showSkillModal = ref(false)
const skillSearchText = ref('')
const emptyMode = ref<'conv' | 'kb' | ''>('')
const showMoreConv = ref(false)
const showMoreKb = ref(false)
const convKbMap = ref<Record<string, string>>({})

const convPreview = computed(() => conversations.value.slice(0, 3))
const convHasMore = computed(() => conversations.value.length > 3)
// ── 对话历史 modal 分页 ──
const convPage = ref(1)
const convPageSize = 8
const pagedConversations = computed(() => {
  const start = (convPage.value - 1) * convPageSize
  return conversations.value.slice(start, start + convPageSize)
})
const convTotalPages = computed(() => Math.max(1, Math.ceil(conversations.value.length / convPageSize)))
watch(showMoreConv, (v) => { if (v) convPage.value = 1 })
const kbPreview = computed(() => kbs.value.slice(0, 3))
const kbHasMore = computed(() => kbs.value.length > 3)

const filteredSkills = computed(() =>
  skills.value.filter((s: Skill) =>
    s.is_active && (!skillSearchText.value || s.name.toLowerCase().includes(skillSearchText.value.toLowerCase()))
  )
)
const selectedSkillName = computed(() => {
  if (!selectedSkillId.value) return '自动选择技能'
  return skills.value.find(s => s.id === selectedSkillId.value)?.name || '自动选择技能'
})


const showPicker = computed(() => emptyMode.value !== '' && messages.value.length === 0 && !conversationId.value)

const selectedKb = computed(() => kbs.value.find((k: any) => k.id === selectedKbId.value))
const currentKbName = computed(() => selectedKb.value?.name || '选择知识库')

function selectAndClose(convId: string) {
  emptyMode.value = ''
  showMoreConv.value = false
  router.push(`/chat/${convId}`)
}

function onKbPick(id: string | null) {
  if (id) selectedKbId.value = id
  showMoreKb.value = false
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

  // Load skills for the skill selector
  try {
    const skillRes = await listSkills(1, 100)
    skills.value = skillRes.items
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

async function doStream(query: string, proxyMsg: ChatMsg, userMsgId: string, skipCache = false) {
  const aid = proxyMsg.id
  let streamedText = ''
  queuePosition.value = null
  abortCtl = new AbortController()
  try {
    for await (const event of streamChat(query, selectedKbId.value, conversationId.value, selectedSkillId.value || undefined, abortCtl.signal, skipCache)) {
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
  doStream(userMsg.content, proxyMsg, userMsg.id, true)
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
    <PageHeader title="RAG 对话" :icon="Chatbubbles">
      <template #actions>
        <NTag v-if="isReadonly" type="info">📖 只读模式 — 查看用户对话</NTag>
        <NButton size="small" @click="showMoreConv = true">
          <template #icon><NIcon size="16"><List /></NIcon></template>
          对话历史
        </NButton>
        <NButton v-if="!isReadonly" size="small" type="primary" @click="newConversation">
          <template #icon><NIcon size="16"><Add /></NIcon></template>
          新建对话
        </NButton>
      </template>
    </PageHeader>

    <div class="chat-messages" ref="messagesContainer" @scroll="onScroll" role="log" aria-live="polite" aria-label="对话消息">
      <!-- Centered panel: conversation list preview -->
      <div v-if="showPicker && emptyMode === 'conv'" class="center-panel">
        <div class="center-panel-box">
          <div class="center-panel-head">
            <p class="center-panel-subtitle">从最近对话继续，或开启新的对话</p>
          </div>
          <div class="conv-list">
            <div v-for="c in convPreview" :key="c.id" class="conv-row"
              role="button" tabindex="0"
              @click="selectAndClose(c.id)"
              @keydown.enter.prevent="selectAndClose(c.id)"
              @keydown.space.prevent="selectAndClose(c.id)"
            >
              <div class="conv-row-avatar">💬</div>
              <div class="conv-row-body">
                <div class="conv-row-title">{{ c.title || '新对话' }}</div>
                <div class="conv-row-meta">
                  <span>{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) }}</span>
                  <span v-if="c.message_count" class="conv-row-count">{{ c.message_count }} 条消息</span>
                </div>
              </div>
            </div>
          </div>
          <NButton v-if="convHasMore" text size="small" type="primary" class="conv-more-btn" @click="showMoreConv = true">
            更多对话 ({{ conversations.length }}) →
          </NButton>
          <NEmpty v-if="conversations.length === 0" description="暂无对话记录" style="padding:8px 0" />
          <div class="conv-fallback">
            或者<NButton text type="primary" @click="emptyMode = 'kb'" style="padding:0 3px;height:auto;vertical-align:baseline;font-size:inherit">新建对话</NButton>
          </div>
        </div>
      </div>

      <!-- Centered panel: KB list preview -->
      <div v-else-if="showPicker && emptyMode === 'kb'" class="center-panel">
        <div class="center-panel-box" :class="{ 'center-panel-box-wide': emptyMode === 'kb' }">
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
              <div class="kb-pick-inner">
                <div class="kb-pick-avatar">📚</div>
                <div class="kb-pick-body">
                  <strong class="kb-pick-name">{{ kb.name }}</strong>
                  <span v-if="kb.description" class="kb-pick-desc">{{ kb.description }}</span>
                  <div class="kb-pick-stats">
                    <span class="kb-pick-chip">{{ kb.doc_count }} 文档</span>
                    <span class="kb-pick-chip">{{ kb.vector_count }} 分片</span>
                  </div>
                </div>
              </div>
            </NCard>
          </div>
          <NButton v-if="kbHasMore" text size="small" type="primary" @click="showMoreKb = true">
            更多知识库 ({{ kbs.length }})
          </NButton>
          <div v-if="kbs.length === 0" class="picker-empty">
            <NEmpty description="还没有知识库" style="padding:8px 0" />
            <NButton type="primary" dashed size="small" @click="router.push('/documents')">
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
        <div v-for="c in pagedConversations" :key="c.id" class="conv-row"
          role="button" tabindex="0"
          @click="selectAndClose(c.id)"
          @keydown.enter.prevent="selectAndClose(c.id)"
          @keydown.space.prevent="selectAndClose(c.id)"
        >
          <div class="conv-row-avatar">💬</div>
          <div class="conv-row-body">
            <div class="conv-row-title">{{ c.title || '新对话' }}</div>
            <div class="conv-row-meta">
              <span>{{ new Date(c.updated_at).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) }}</span>
              <span v-if="c.message_count" class="conv-row-count">{{ c.message_count }} 条消息</span>
            </div>
          </div>
        </div>
      </div>
      <NPagination
        v-if="convTotalPages > 1"
        v-model:page="convPage"
        :item-count="conversations.length"
        :page-size="convPageSize"
        simple
        class="conv-pager"
      />
    </NModal>

    <!-- Modal: KB picker with search -->
    <KbPickerModal
      v-model:show="showMoreKb"
      :kbs="kbs"
      :selected-id="selectedKbId"
      :show-all="false"
      :sortable="true"
      :page-size="12"
      @select="onKbPick"
    />

    <NModal v-model:show="showSkillModal" preset="card" title="选择技能"
      style="width: 92vw; max-width: 680px"
      @after-leave="skillSearchText = ''"
    >
      <NInput v-model:value="skillSearchText" placeholder="搜索技能名称..." clearable style="margin-bottom:12px" />
      <div class="skill-pick-grid">
        <NCard size="small" class="skill-pick-card"
          :class="{ active: !selectedSkillId }"
          role="button" tabindex="0"
          @click="selectedSkillId = null; showSkillModal = false"
          @keydown.enter.prevent="selectedSkillId = null; showSkillModal = false"
          @keydown.space.prevent="selectedSkillId = null; showSkillModal = false"
        >
          <div class="skill-pick-header">
            <div class="skill-pick-title-wrap">
              <span class="skill-pick-name">自动选择技能</span>
            </div>
          </div>
          <p class="skill-pick-desc">根据问题自动路由最合适的技能</p>
          <div class="skill-pick-tools">
            <span class="skill-pick-label">工具</span>
            <span class="skill-pick-tool-muted">自动</span>
          </div>
        </NCard>
        <NCard v-for="s in filteredSkills" :key="s.id" size="small" class="skill-pick-card"
          :class="{ active: s.id === selectedSkillId }"
          role="button" tabindex="0"
          @click="selectedSkillId = s.id; showSkillModal = false"
          @keydown.enter.prevent="selectedSkillId = s.id; showSkillModal = false"
          @keydown.space.prevent="selectedSkillId = s.id; showSkillModal = false"
        >
          <div class="skill-pick-header">
            <div class="skill-pick-title-wrap">
              <span class="skill-pick-name" :title="s.name">{{ s.name }}</span>
              <NTag v-if="!s.is_active" size="tiny" :bordered="false" type="default" class="skill-pick-disabled-tag">禁用</NTag>
            </div>
          </div>
          <p class="skill-pick-desc" :title="s.description ?? undefined">{{ s.description || '暂无描述' }}</p>
          <div class="skill-pick-tools">
            <span class="skill-pick-label">工具</span>
            <template v-if="s.mcp_servers && s.mcp_servers.length">
              <NTag
                v-for="t in s.mcp_servers"
                :key="t"
                size="tiny"
                type="info"
                :bordered="false"
                class="skill-pick-tool-tag"
              >{{ t }}</NTag>
            </template>
            <span v-else class="skill-pick-tool-muted">无</span>
          </div>
        </NCard>
      </div>
      <NEmpty v-if="filteredSkills.length === 0" description="没有匹配的技能" style="padding:16px 0" />
    </NModal>

    <div v-if="!isReadonly" class="chat-input-wrapper">
      <div class="skill-selector-bar">
        <NButton size="tiny" ghost class="kb-trigger-btn" @click="showMoreKb = true">
          {{ currentKbName }}
        </NButton>
        <NButton size="tiny" ghost class="skill-selector-btn" @click="showSkillModal = true">
          <template #icon><NIcon size="14"><Sparkles /></NIcon></template>
          {{ selectedSkillName }}
        </NButton>
      </div>
      <div class="chat-input-area">
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
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin: 12px 0 8px;
  text-align: left;
}
/* KB 预览面板单独加宽，以容纳 3 列网格（与 KbPickerModal 观感一致），不影响对话面板 */
.center-panel-box-wide { max-width: 680px; }
@media (max-width: 640px) {
  .center-panel-list { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 420px) {
  .center-panel-list { grid-template-columns: 1fr; }
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

/* ── Optimized conversation history list (中间「选择一个对话」面板) ── */
.center-panel-head {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 20px;
}
.center-panel-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-xl);
  background: var(--color-primary-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  margin-bottom: 14px;
}
.center-panel-subtitle {
  font-size: var(--text-base);
  color: var(--color-text-muted);
  margin-top: 4px;
}
.conv-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
  text-align: left;
}
.conv-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.conv-row:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.conv-row:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.conv-row-avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--radius);
  background: var(--color-primary-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.conv-row-body {
  flex: 1;
  min-width: 0;
}
.conv-row-title {
  font-size: 14px;
  font-weight: 400;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 6px;
}
.conv-row-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.conv-row-count {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 500;
}
.conv-more-btn {
  margin-bottom: 4px;
}
.conv-fallback {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

/* Modal scrollable list (shared by "更多对话" and "更多知识库") */
.picker-scroll { max-height: 60vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.conv-pager {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}

.kb-pick-card {
  cursor: pointer;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, background .15s ease, transform .15s ease;
}
.kb-pick-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.kb-pick-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.kb-pick-card.active { border-color: var(--color-primary); background: var(--color-primary-soft); }
.kb-pick-inner { display: flex; align-items: flex-start; gap: 10px; }
.kb-pick-avatar {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  background: var(--color-primary-soft);
}
.kb-pick-body { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.kb-pick-name { font-size: 14px; font-weight: 600; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-desc { font-size: var(--text-xs); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-stats { display: flex; flex-wrap: wrap; gap: 6px; }
.kb-pick-chip {
  font-size: 0.7rem; line-height: 1.4;
  color: var(--color-text-muted);
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border);
  border-radius: 9999px;
  padding: 1px 8px;
}
.picker-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 8px 0; }
.picker-footer-hint { font-size: var(--text-xs); color: var(--color-text-muted); }
.picker-footer-hint strong { color: var(--color-text); }
.fallback-hint { margin-top: 8px; font-size: var(--text-base); color: var(--color-text-muted); }
/* ── Skill picker modal（与 SkillsView .sk-card 同款卡片，3 栏网格）── */
/* 卡片 chrome 与 .sk-card 统一（亮/暗 token 驱动） */
.skill-pick-card {
  cursor: pointer;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, background .15s ease, transform .15s ease;
}
.skill-pick-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.skill-pick-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
/* 选中态：与 .sk-card 的 hover/focus 一致——主色边框 + 主色柔光底 */
.skill-pick-card.active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}
/* 3 栏网格：宽度一致，一行 3 个 */
.skill-pick-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
  padding: 2px;
}
.skill-pick-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}
.skill-pick-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.skill-pick-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-pick-desc {
  margin: 0 0 8px;
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.4em;
}
.skill-pick-tools {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.skill-pick-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.skill-pick-tool-tag {
  margin-right: 2px;
}
.skill-pick-tool-muted {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

/* ── Chat input wrapper with skill selector above ── */
.chat-input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3) 0 0;
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}
.skill-selector-bar {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  padding: 0 0 6px;
}
.skill-selector-btn {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 输入框上方的 KB / 技能选择按钮：字号从 tiny 略放大到 13px，更易读 */
.skill-selector-bar :deep(.n-button) {
  font-size: 13px;
}
.chat-input-area {
  display: flex;
  gap: var(--space-2);
  padding: 0 0 var(--space-3);
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
  font-weight: 700;
}

/* ── Mobile: header wraps gracefully ── */
@media (max-width: 767px) {
  .kb-trigger-btn {
    max-width: 120px;
  }
  .scroll-bottom-btn {
    right: 14px;
    bottom: 78px;
  }
}
</style>
