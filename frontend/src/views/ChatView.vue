<script setup lang="ts">
import { ref, nextTick, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { currentLocale } from '@/i18n/useLocale'
import { NInput, NButton, NIcon, NTag, NCard, NEmpty, NSpace, useMessage } from 'naive-ui'
import KbPickerModal from '@/components/kb/KbPickerModal.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import { Send, StopCircle, Chatbubbles, List, Add, ChevronDown, Sparkles, Search, Close } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, getConversation, getConversationMessages, listConversations } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { listKnowledgeBases } from '@/api/documents'
import { listSkills } from '@/api/skills'
import { renderStreamingHtml } from '@/utils/think'
import type { ChatMessage as ChatMsg, Skill } from '@/types'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
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
// 服务端分页：每页 PAGE_SIZE_ROUNDS 轮（一问一答为一轮 = 2 条消息）。
// 打开对话时加载最新一页（最后一页），向上滚动触顶时再请求上一页并拼接到顶部。
const PAGE_SIZE_ROUNDS = 10
const currentPage = ref(1)
const totalPages = ref(1)
const totalRounds = ref(0)
const isLoadingOlder = ref(false)
const hasMoreOlder = computed(() => currentPage.value > 1)
const inputText = ref('')
const isStreaming = ref(false)
const queuePosition = ref<number | null>(null)
// 挂起提示（后端命中上限后推送 need_user_input）：{ 文案, 后端给的 conv_id, 原因 }
const pendingLimit = ref<{ message: string; convId: string; kind: string } | null>(null)
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
  // 向上滚动触顶 → 自动加载更早的对话（上一页）
  if (el.scrollTop <= 48 && hasMoreOlder.value && !isLoadingOlder.value) {
    loadOlder()
  }
}

// 向前加载更早的对话：向服务端请求上一页，拼接到列表顶部，并补偿滚动位置避免画面跳动
async function loadOlder() {
  if (!hasMoreOlder.value || isLoadingOlder.value || !conversationId.value) return
  isLoadingOlder.value = true
  const el = messagesContainer.value
  const prevHeight = el ? el.scrollHeight : 0
  const prevPage = currentPage.value - 1
  try {
    const data = await getConversationMessages(conversationId.value, prevPage, PAGE_SIZE_ROUNDS)
    messages.value = [...data.messages, ...messages.value]
    currentPage.value = data.page
    totalPages.value = data.total_pages
    totalRounds.value = data.total_rounds
  } catch {
    // 加载失败不改变现有展示
  } finally {
    await nextTick()
    if (el && el.isConnected) {
      const added = el.scrollHeight - prevHeight
      el.scrollTop = el.scrollTop + added
    }
    isLoadingOlder.value = false
  }
}

// 加载对话的最新一页（最后一页），并滚动到底部
async function loadInitialPage(id: string) {
  const data = await getConversationMessages(id, 'last', PAGE_SIZE_ROUNDS)
  messages.value = data.messages
  currentPage.value = data.page
  totalPages.value = data.total_pages
  totalRounds.value = data.total_rounds
  isLoadingOlder.value = false
  isPinnedToBottom.value = true
  await nextTick()
  await scrollToBottom()
}

function scrollToBottomAndPin() {
  isPinnedToBottom.value = true
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

const showScrollBottomBtn = computed(() => !isPinnedToBottom.value && messages.value.length > 0)

// ── 查找对话记录：仅对当前已加载的消息做关键字匹配 ──
const showSearch = ref(false)
const searchKeyword = ref('')
const searchMatches = ref<string[]>([])   // 命中的消息 id（按出现顺序）
const currentMatchIndex = ref(-1)
const searchInputRef = ref<any>(null)
const searchKw = computed(() => searchKeyword.value.trim())
const searchActive = computed(() => showSearch.value && searchKw.value.length > 0)
const activeMatchId = computed(() =>
  searchActive.value && currentMatchIndex.value >= 0 ? searchMatches.value[currentMatchIndex.value] : ''
)

function computeMatches() {
  const kw = searchKw.value.toLowerCase()
  if (!kw) {
    searchMatches.value = []
    currentMatchIndex.value = -1
    return
  }
  const ids: string[] = []
  for (const m of messages.value) {
    if ((m.content || '').toLowerCase().includes(kw)) ids.push(m.id)
  }
  searchMatches.value = ids
  currentMatchIndex.value = ids.length > 0 ? 0 : -1
}

function searchNext() {
  if (!searchMatches.value.length) return
  currentMatchIndex.value = (currentMatchIndex.value + 1) % searchMatches.value.length
}
function searchPrev() {
  if (!searchMatches.value.length) return
  currentMatchIndex.value = (currentMatchIndex.value - 1 + searchMatches.value.length) % searchMatches.value.length
}
function openSearch() {
  showSearch.value = true
  nextTick(() => searchInputRef.value?.focus())
}
function closeSearch() {
  showSearch.value = false
  searchKeyword.value = ''
  searchMatches.value = []
  currentMatchIndex.value = -1
}

watch(searchKeyword, computeMatches)
watch(activeMatchId, (id) => {
  if (!id) return
  nextTick(() => {
    const el = document.getElementById('msg-' + id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
})
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
  if (!selectedSkillId.value) return t('chat.autoSelectSkill')
  return skills.value.find(s => s.id === selectedSkillId.value)?.name || t('chat.autoSelectSkill')
})


const showPicker = computed(() => emptyMode.value !== '' && messages.value.length === 0 && !conversationId.value)

const selectedKb = computed(() => kbs.value.find((k: any) => k.id === selectedKbId.value))
const currentKbName = computed(() => selectedKb.value?.name || t('chat.selectKb'))

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
    currentPage.value = 1
    totalPages.value = 1
    totalRounds.value = 0
    conversationId.value = undefined
    isReadonly.value = false
    emptyMode.value = 'conv'
    await loadConversations()
  }
})

async function loadConversation(id: string) {
  try {
    // 仅获取会话元数据（不含消息），消息由服务端分页接口加载
    const conv = await getConversation(id, false)
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
    // 服务端分页：加载最新一页（最后一页）并滚动到底部
    await loadInitialPage(id)
  } catch {
    messages.value = []
    conversationId.value = undefined
    currentPage.value = 1
    totalPages.value = 1
    totalRounds.value = 0
    router.replace('/chat')
  }
}

// Listen for reset-chat event from sidebar
onMounted(() => {
  window.addEventListener('erag:reset-chat', () => {
    isReadonly.value = false
    conversationId.value = undefined
    messages.value = []
    currentPage.value = 1
    totalPages.value = 1
    totalRounds.value = 0
    emptyMode.value = 'conv'
    loadConversations()
  })
})

async function doStream(query: string, proxyMsg: ChatMsg, userMsgId: string, skipCache = false, resumeAction: 'continue' | 'stop' | null = null) {
  const aid = proxyMsg.id
  let streamedText = ''
  queuePosition.value = null
  abortCtl = new AbortController()
  try {
    for await (const event of streamChat(query, selectedKbId.value, conversationId.value, selectedSkillId.value || undefined, abortCtl.signal, skipCache, resumeAction)) {
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
      } else if (event.type === 'agent_step') {
        if (!proxyMsg.agentSteps) proxyMsg.agentSteps = []
        proxyMsg.agentSteps.push(event)
      } else if (event.type === 'error') {
        streamedText = t('chat.streamError', { msg: event.message })
        break
      } else if (event.type === 'need_user_input') {
        // 挂起：保存提示，移除本轮空白助手气泡；继续/停止时重建气泡
        pendingLimit.value = { message: event.message, convId: event.conv_id, kind: event.kind }
        messages.value = messages.value.filter(m => m.id !== proxyMsg.id)
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
      nmessage.error(t('chat.sendFailed', { msg: e.message }))
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
  // 挂起态下用户输入新问题：强制带上同一 conv_id，后端会视为「停止」并丢弃挂起
  if (pendingLimit.value) conversationId.value = pendingLimit.value.convId
  pendingLimit.value = null

  const userMsg: ChatMsg = { id: crypto.randomUUID(), role: 'user', content: text, citations: [], created_at: new Date().toISOString() }
  messages.value.push(userMsg)
  inputText.value = ''

  const assistantMsg: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], agentSteps: [], created_at: new Date().toISOString() }
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
  const newAssistant: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], agentSteps: [], created_at: new Date().toISOString() }
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

// 挂起恢复：继续（追加额度后重放被拒调用）/ 停止（用已累计结果出答案）
async function resumeRun(action: 'continue' | 'stop') {
  const pl = pendingLimit.value
  if (!pl || isStreaming.value) return
  const convId = pl.convId
  pendingLimit.value = null
  conversationId.value = convId
  const assistantMsg: ChatMsg = { id: crypto.randomUUID(), role: 'assistant', content: '', citations: [], agentSteps: [], created_at: new Date().toISOString() }
  messages.value.push(assistantMsg)
  const proxyMsg = messages.value[messages.value.length - 1]
  isStreaming.value = true
  isPinnedToBottom.value = true
  await scrollToBottom()
  await nextTick()
  doStream('', proxyMsg, assistantMsg.id, false, action)
}

function continueResume() {
  resumeRun('continue')
}

function stopResume() {
  resumeRun('stop')
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
  currentPage.value = 1
  totalPages.value = 1
  totalRounds.value = 0
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
    <PageHeader :title="t('chat.title')" :icon="Chatbubbles">
      <template #actions>
        <NTag v-if="isReadonly" type="info">{{ t('chat.readonlyMode') }}</NTag>
        <NButton size="small" @click="showMoreConv = true">
          <template #icon><NIcon size="16"><List /></NIcon></template>
          {{ t('chat.history') }}
        </NButton>
        <NButton v-if="!isReadonly" size="small" type="primary" @click="newConversation">
          <template #icon><NIcon size="16"><Add /></NIcon></template>
          {{ t('chat.newConversation') }}
        </NButton>
      </template>
    </PageHeader>

    <div class="chat-messages" ref="messagesContainer" @scroll="onScroll" role="log" aria-live="polite" :aria-label="t('chat.ariaMessages')">
      <!-- Centered panel: conversation list preview -->
      <div v-if="showPicker && emptyMode === 'conv'" class="center-panel">
        <div class="center-panel-box">
          <div class="center-panel-head">
            <p class="center-panel-subtitle">{{ t('chat.continueOrStart') }}</p>
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
                <div class="conv-row-title">{{ c.title || t('chat.untitledConversation') }}</div>
                <div class="conv-row-meta">
                  <span>{{ new Intl.DateTimeFormat(currentLocale, { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }).format(new Date(c.updated_at)) }}</span>
                  <span v-if="c.message_count" class="conv-row-count">{{ t('chat.messageCount', { count: c.message_count }) }}</span>
                </div>
              </div>
            </div>
          </div>
          <NButton v-if="convHasMore" text size="small" type="primary" class="conv-more-btn" @click="showMoreConv = true">
            {{ t('chat.moreConversations', { count: conversations.length }) }}
          </NButton>
          <NEmpty v-if="conversations.length === 0" :description="t('chat.noConversations')" style="padding:8px 0" />
          <div class="conv-fallback">
            {{ t('chat.or') }}<NButton text type="primary" @click="emptyMode = 'kb'" style="padding:0 3px;height:auto;vertical-align:baseline;font-size:inherit">{{ t('chat.newConversation') }}</NButton>
          </div>
        </div>
      </div>

      <!-- Centered panel: KB list preview -->
      <div v-else-if="showPicker && emptyMode === 'kb'" class="center-panel">
        <div class="center-panel-box" :class="{ 'center-panel-box-wide': emptyMode === 'kb' }">
          <div class="empty-icon">🧠</div>
          <h3>{{ t('chat.newConversationPickKb') }}</h3>
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
                    <span class="kb-pick-chip">{{ t('chat.docCount', { count: kb.doc_count }) }}</span>
                    <span class="kb-pick-chip">{{ t('chat.chunkCount', { count: kb.vector_count }) }}</span>
                  </div>
                </div>
              </div>
            </NCard>
          </div>
          <NButton v-if="kbHasMore" text size="small" type="primary" @click="showMoreKb = true">
            {{ t('chat.moreKbs', { count: kbs.length }) }}
          </NButton>
          <div v-if="kbs.length === 0" class="picker-empty">
            <NEmpty :description="t('chat.noKbs')" style="padding:8px 0" />
            <NButton type="primary" dashed size="small" @click="router.push('/documents')">
              {{ t('chat.goCreateKb') }}
            </NButton>
          </div>
          <div class="center-panel-actions">
            <div class="picker-footer-hint">
              {{ t('chat.selectedPrefix') }}<strong>{{ selectedKbId ? (kbs.find(k => k.id === selectedKbId)?.name ?? '...') : t('chat.notSelected') }}</strong>
            </div>
            <NSpace>
              <NButton v-if="conversations.length > 0" @click="emptyMode = 'conv'">{{ t('chat.back') }}</NButton>
              <NButton type="primary" @click="emptyMode = ''" :disabled="!selectedKbId">{{ t('chat.startChat') }}</NButton>
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
          <p v-else>{{ t('chat.inputQuestionToStart') }}</p>
          <div class="center-panel-actions" style="margin-top:12px; gap:4px; justify-content:center">
            <NButton size="small" @click="emptyMode = 'kb'">{{ t('chat.changeKb') }}</NButton>
          </div>
        </template>
      </div>

      <!-- Fallback empty: no conversation, picker not yet opened -->
      <div v-else-if="messages.length === 0 && !conversationId" class="empty-state">
        <div class="empty-icon">💬</div>
        <h3>{{ t('chat.startChat') }}</h3>
        <p>{{ t('chat.selectOrStart') }}</p>
        <NButton type="primary" size="small" @click="emptyMode = 'conv'" style="margin-top:8px">
          {{ t('chat.selectConversation') }}
        </NButton>
        <p class="fallback-hint">{{ t('chat.or') }}<NButton text size="tiny" type="primary" @click="emptyMode = 'kb'" style="padding:0 2px;height:auto;vertical-align:baseline">{{ t('chat.newConversation') }}</NButton></p>
      </div>

      <!-- Edge case: conversation loaded but no messages -->
      <div v-else-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🔍</div>
        <h3>{{ t('chat.emptyConversation') }}</h3>
        <p>{{ t('chat.inputToStart') }}</p>
      </div>
      <!-- 分页提示：向上滚动到顶时自动加载更早的对话 -->
      <div v-if="totalRounds > 0" class="history-sentinel" aria-live="polite">
        <span v-if="isLoadingOlder" class="history-sentinel-spinner" aria-hidden="true"></span>
        <span v-if="isLoadingOlder">{{ t('chat.loadingOlder') }}</span>
        <span v-else-if="!hasMoreOlder" class="history-sentinel-done">{{ t('chat.allShown', { count: totalRounds }) }}</span>
      </div>
      <ChatMessage
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
        :queue-position="queuePosition"
        :search-keyword="searchKw"
        :active-match="msg.id === activeMatchId"
        @regenerate="regenerateAnswer"
      />
    </div>

    <Transition name="scroll-btn">
      <button
        v-if="showScrollBottomBtn && !showSearch"
        class="scroll-bottom-btn"
        :class="{ streaming: isStreaming }"
        @click="scrollToBottomAndPin"
        :title="t('chat.scrollToBottom')"
        :aria-label="t('chat.scrollToBottom')"
      >
        <NIcon size="20"><ChevronDown /></NIcon>
      </button>
    </Transition>

    <!-- 浮动搜索条：查找已加载的对话记录 -->
    <Transition name="search-pop">
      <div v-if="showSearch" class="search-bar">
        <div class="search-bar-inner">
          <NInput
            ref="searchInputRef"
            v-model:value="searchKeyword"
            :placeholder="t('chat.searchPlaceholder')"
            clearable
            size="small"
            class="search-input"
            @keydown.esc="closeSearch"
          >
            <template #prefix><NIcon size="14"><Search /></NIcon></template>
          </NInput>
          <span class="search-counter">{{ searchMatches.length ? (currentMatchIndex + 1) + ' / ' + searchMatches.length : '0 / 0' }}</span>
          <NButton size="small" :disabled="!searchMatches.length" @click="searchPrev">{{ t('chat.prev') }}</NButton>
          <NButton size="small" :disabled="!searchMatches.length" @click="searchNext">{{ t('chat.next') }}</NButton>
          <NButton size="small" quaternary circle :title="t('chat.closeSearch')" :aria-label="t('chat.closeSearch')" @click="closeSearch">
            <template #icon><NIcon size="16"><Close /></NIcon></template>
          </NButton>
        </div>
      </div>
    </Transition>

    <!-- Modal: full conversation list -->
    <AppModal v-model:show="showMoreConv" :title="t('chat.allConversations')" size="detail">
      <div class="picker-scroll">
        <div v-for="c in pagedConversations" :key="c.id" class="conv-row"
          role="button" tabindex="0"
          @click="selectAndClose(c.id)"
          @keydown.enter.prevent="selectAndClose(c.id)"
          @keydown.space.prevent="selectAndClose(c.id)"
        >
          <div class="conv-row-avatar">💬</div>
          <div class="conv-row-body">
            <div class="conv-row-title">{{ c.title || t('chat.untitledConversation') }}</div>
            <div class="conv-row-meta">
              <span>{{ new Intl.DateTimeFormat(currentLocale, { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }).format(new Date(c.updated_at)) }}</span>
              <span v-if="c.message_count" class="conv-row-count">{{ t('chat.messageCount', { count: c.message_count }) }}</span>
            </div>
          </div>
        </div>
      </div>
      <AppPagination
        v-model:page="convPage"
        :page-size="convPageSize"
        :item-count="conversations.length"
        simple
        align="center"
      />
    </AppModal>

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

    <AppModal v-model:show="showSkillModal" :title="t('chat.selectSkill')"
      size="wide"
      @after-leave="skillSearchText = ''"
    >
      <NInput v-model:value="skillSearchText" :placeholder="t('chat.searchSkillPlaceholder')" clearable style="margin-bottom:12px" />
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
              <span class="skill-pick-name">{{ t('chat.autoSelectSkill') }}</span>
            </div>
          </div>
          <p class="skill-pick-desc">{{ t('chat.autoSelectSkillDesc') }}</p>
          <div class="skill-pick-tools">
            <span class="skill-pick-label">{{ t('chat.tools') }}</span>
            <span class="skill-pick-tool-muted">{{ t('chat.auto') }}</span>
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
              <NTag v-if="!s.is_active" size="tiny" :bordered="false" type="default" class="skill-pick-disabled-tag">{{ t('common.disabled') }}</NTag>
            </div>
          </div>
          <p class="skill-pick-desc" :title="s.description ?? undefined">{{ s.description || t('chat.noDescription') }}</p>
          <div class="skill-pick-tools">
            <span class="skill-pick-label">{{ t('chat.tools') }}</span>
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
            <span v-else class="skill-pick-tool-muted">{{ t('chat.none') }}</span>
          </div>
        </NCard>
      </div>
      <NEmpty v-if="filteredSkills.length === 0" :description="t('chat.noMatchingSkill')" style="padding:16px 0" />
    </AppModal>

    <div v-if="!isReadonly" class="chat-input-wrapper">
      <div class="skill-selector-bar">
        <NButton size="tiny" ghost class="kb-trigger-btn" @click="showMoreKb = true">
          {{ currentKbName }}
        </NButton>
        <NButton size="tiny" ghost class="skill-selector-btn" @click="showSkillModal = true">
          <template #icon><NIcon size="14"><Sparkles /></NIcon></template>
          {{ selectedSkillName }}
        </NButton>
        <NButton size="tiny" ghost class="search-trigger-btn" :type="showSearch ? 'primary' : 'default'" @click="showSearch ? closeSearch() : openSearch()">
          <template #icon><NIcon size="14"><Search /></NIcon></template>
          {{ t('chat.findRecords') }}
        </NButton>
      </div>
      <div v-if="pendingLimit" class="resume-banner" role="alert">
        <div class="resume-banner-msg">{{ pendingLimit.message }}</div>
        <NSpace align="center">
          <NButton type="primary" :loading="isStreaming" @click="continueResume">
            {{ t('chat.continueResume') }}
          </NButton>
          <NButton :disabled="isStreaming" @click="stopResume">
            {{ t('chat.stopResume') }}
          </NButton>
        </NSpace>
        <div class="resume-banner-hint">{{ t('chat.resumeHint') }}</div>
      </div>
      <div class="chat-input-area">
        <NInput
          v-model:value="inputText"
          type="textarea"
          :placeholder="auth.llmConfigured ? t('chat.inputPlaceholder') : t('chat.configApiKey')"
          :autosize="{ minRows: 1, maxRows: 4 }"
          :disabled="isStreaming || !auth.llmConfigured"
          @keydown="handleKeydown"
          @compositionstart="isComposing = true"
          @compositionend="isComposing = false"
        />
        <NButton v-if="queuePosition != null && queuePosition > 0" type="warning" @click="cancelQueue">
          <template #icon><NIcon><StopCircle /></NIcon></template>
          {{ t('chat.cancelQueue') }}
        </NButton>
        <NButton v-else-if="isStreaming" type="warning" @click="stopStream">
          <template #icon><NIcon><StopCircle /></NIcon></template>
          {{ t('chat.stop') }}
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

/* ── 挂起提示横幅（命中上限时）── */
.resume-banner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0 var(--space-3) var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--color-card-bg);
  border: 1px solid var(--color-border, #e5e7eb);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.resume-banner-msg {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.5;
}
.resume-banner-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
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

/* ── 浮动搜索条（查找对话记录）── */
.search-bar {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 92px;
  z-index: 30;
  display: flex;
  justify-content: center;
  pointer-events: none;
}
.search-bar-inner {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 640px;
  padding: 8px 10px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}
.search-input {
  flex: 1;
  min-width: 0;
}
.search-counter {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.search-trigger-btn {
  font-weight: 700;
}
.search-pop-enter-active,
.search-pop-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.search-pop-enter-from,
.search-pop-leave-to {
  opacity: 0;
  transform: translateY(8px);
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
  .search-bar {
    left: 8px;
    right: 8px;
    bottom: 84px;
  }
  .search-bar-inner {
    gap: 6px;
    padding: 6px 8px;
  }
  .search-counter {
    display: none;
  }
}

/* ── 对话分页提示（顶部加载更早 / 已到顶）── */
.history-sentinel {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 8px;
  margin: 2px 0 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  text-align: center;
}
.history-sentinel-done {
  opacity: 0.75;
}
.history-sentinel-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: history-sentinel-spin 0.7s linear infinite;
}
@keyframes history-sentinel-spin {
  to { transform: rotate(360deg); }
}
</style>
