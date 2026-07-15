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
import { streamChat, getConversation, getConversationMessages, getPendingLimit, listConversations } from '@/api/chat'
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
// Server-side pagination: PAGE_SIZE_ROUNDS rounds per page (one Q&A = one round = 2 messages).
// When opening a conversation, load the newest page (last page); when scrolling up to the top, request the previous page and prepend it.
const PAGE_SIZE_ROUNDS = 10
const currentPage = ref(1)
const totalPages = ref(1)
const totalRounds = ref(0)
const isLoadingOlder = ref(false)
const hasMoreOlder = computed(() => currentPage.value > 1)
const inputText = ref('')
const isStreaming = ref(false)
const queuePosition = ref<number | null>(null)
// LLM context token count: total tokens of the latest request body (system prompt + history + RAG + memory + tools + question)
const contextTokens = ref(0)
// Suspension hint (pushed by the backend as need_user_input when the limit is hit): { copy, backend-supplied conv_id, reason }
const pendingLimit = ref<{ message: string; convId: string; kind: string; messageId: string } | null>(null)
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
  // Scroll up to the top → automatically load earlier conversations (previous page)
  if (el.scrollTop <= 48 && hasMoreOlder.value && !isLoadingOlder.value) {
    loadOlder()
  }
}

// Load earlier conversations forward: request the previous page from the server, prepend it to the list, and compensate the scroll position to avoid jumps
// A manually terminated round in history (status==='stopped'): the DB stores only the original hint copy,
// and at load time overlays a localized termination notice based on the current UI language, so it still shows after refresh.
function applyStoppedNote(msgs: ChatMsg[]): ChatMsg[] {
  return msgs.map(m =>
    m.status === 'stopped'
      ? { ...m, content: (m.content || '') + '\n\n' + t('chat.userStoppedNote') }
      : m,
  )
}

async function loadOlder() {
  if (!hasMoreOlder.value || isLoadingOlder.value || !conversationId.value) return
  isLoadingOlder.value = true
  const el = messagesContainer.value
  const prevHeight = el ? el.scrollHeight : 0
  const prevPage = currentPage.value - 1
  try {
    const data = await getConversationMessages(conversationId.value, prevPage, PAGE_SIZE_ROUNDS)
    messages.value = [...applyStoppedNote(data.messages), ...messages.value]
    currentPage.value = data.page
    totalPages.value = data.total_pages
    totalRounds.value = data.total_rounds
    syncContextFromMessages()
  } catch {
    // On load failure, do not change the current display
  } finally {
    await nextTick()
    if (el && el.isConnected) {
      const added = el.scrollHeight - prevHeight
      el.scrollTop = el.scrollTop + added
    }
    isLoadingOlder.value = false
  }
}

// Restore the context token count from loaded messages (take the last assistant message that has token_count)
function syncContextFromMessages() {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]
    if (m.role === 'assistant' && typeof m.token_count === 'number' && m.token_count > 0) {
      contextTokens.value = m.token_count
      return
    }
  }
  contextTokens.value = 0
}

// Load the conversation's newest page (last page) and scroll to the bottom
async function loadInitialPage(id: string) {
  const data = await getConversationMessages(id, 'last', PAGE_SIZE_ROUNDS)
  messages.value = applyStoppedNote(data.messages)
  currentPage.value = data.page
  totalPages.value = data.total_pages
  totalRounds.value = data.total_rounds
  isLoadingOlder.value = false
  isPinnedToBottom.value = true
  syncContextFromMessages()
  await nextTick()
  await scrollToBottom()
}

function scrollToBottomAndPin() {
  isPinnedToBottom.value = true
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

const showScrollBottomBtn = computed(() => !isPinnedToBottom.value && messages.value.length > 0)

// ── Search conversation history: match keywords only against currently loaded messages ──
const showSearch = ref(false)
const searchKeyword = ref('')
const searchMatches = ref<string[]>([])   // IDs of matched messages (in order of appearance)
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
// ── Conversation history modal pagination ──
const convPage = ref(1)
const convPageSize = 8
const pagedConversations = computed(() => {
  const start = (convPage.value - 1) * convPageSize
  return conversations.value.slice(start, start + convPageSize)
})
const convTotalPages = computed(() => Math.max(1, Math.ceil(conversations.value.length / convPageSize)))
watch(showMoreConv, (v) => { if (v) convPage.value = 1 })

// ── History questions modal: user questions of the current conversation ──
// Backend paginates by "round" (one Q&A = one round = 1 user + 1 assistant message),
// so 10 questions per page maps exactly to page_size=10. We reuse the backend pagination
// instead of loading everything client-side.
const showQuestionsModal = ref(false)
const questions = ref<ChatMsg[]>([])
const questionsPage = ref(1)
const QUESTIONS_PAGE_SIZE = 10
const questionsTotalRounds = ref(0)
const questionsLoading = ref(false)
const questionsListRef = ref<HTMLElement>()
const questionsTotalPages = computed(() => Math.max(1, Math.ceil(questionsTotalRounds.value / QUESTIONS_PAGE_SIZE)))

function scrollQuestionsToBottom() {
  nextTick(() => {
    const el = questionsListRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function openQuestionsModal() {
  if (!conversationId.value) return
  showQuestionsModal.value = true
  questionsLoading.value = true
  try {
    const data = await getConversationMessages(conversationId.value, 'last', QUESTIONS_PAGE_SIZE)
    questions.value = (data.messages || []).filter((m: ChatMsg) => m.role === 'user')
    questionsTotalRounds.value = data.total_rounds ?? 0
    questionsPage.value = data.page ?? questionsTotalPages.value
  } catch {
    questions.value = []
    questionsTotalRounds.value = 0
  } finally {
    questionsLoading.value = false
    scrollQuestionsToBottom()
  }
}

async function onQuestionsPageChange(page: number) {
  if (!conversationId.value) return
  questionsPage.value = page
  questionsLoading.value = true
  try {
    const data = await getConversationMessages(conversationId.value, page, QUESTIONS_PAGE_SIZE)
    questions.value = (data.messages || []).filter((m: ChatMsg) => m.role === 'user')
    questionsTotalRounds.value = data.total_rounds ?? 0
  } catch {
    questions.value = []
  } finally {
    questionsLoading.value = false
    scrollQuestionsToBottom()
  }
}

// Prepend earlier pages into the conversation view until the target message is present (or no more pages)
async function ensureMessageLoaded(id: string) {
  if (messages.value.some((m) => m.id === id)) return
  if (!conversationId.value) return
  // Safety cap: at most (currentPage - 1) earlier pages exist between the
  // current page and page 1, so we never need (or want) to loop more than that.
  const maxLoads = Math.max(1, currentPage.value - 1)
  let loads = 0
  while (!messages.value.some((m) => m.id === id) && hasMoreOlder.value && loads < maxLoads) {
    await loadOlder()
    loads++
  }
}

async function onQuestionClick(id: string) {
  showQuestionsModal.value = false
  await ensureMessageLoaded(id)
  await nextTick()
  const el = document.getElementById('msg-' + id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

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

// ── LLM context token statistics display ──
function formatTokens(n: number): string {
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(n)
}
const contextRatio = computed(() => {
  const w = auth.contextWindow || 1
  return Math.min(1, contextTokens.value / w)
})
const contextRatioPct = computed(() => Math.round(contextRatio.value * 100))
const contextRatioClass = computed(() => {
  const r = contextRatio.value
  if (r >= 0.9) return 'danger'
  if (r >= 0.7) return 'warn'
  return 'ok'
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
    contextTokens.value = 0
    isReadonly.value = false
    emptyMode.value = 'conv'
    await loadConversations()
  }
})

async function loadConversation(id: string) {
  try {
    // Fetch only conversation metadata (no messages); messages are loaded via the server-side pagination API
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
    // Server-side pagination: load the newest page (last page) and scroll to the bottom
    await loadInitialPage(id)
    // Restore suspension state after refresh: if a pending quota suspension awaiting confirmation exists, rebuild the inline bubble and hide that hint message
    const pending = await getPendingLimit(id)
    if (pending && pending.message_id) {
      pendingLimit.value = {
        message: pending.message,
        convId: pending.conversation_id,
        kind: pending.kind,
        messageId: pending.message_id,
      }
      const pm = messages.value.find(m => m.id === pending.message_id)
      if (pm) pm._pending = true
    }
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
    contextTokens.value = 0
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
        // Suspension: the backend has saved an assistant message (the hint copy). The frontend renders it as a real message bubble,
        // and after the user replies, reuses that message id so the backend replaces the content in place with the final answer.
        messages.value = messages.value.filter(m => m.id !== proxyMsg.id)
        const pendingMsg: ChatMsg = {
          id: event.message_id,
          role: 'assistant',
          content: event.message,
          citations: [],
          created_at: new Date().toISOString(),
          agentSteps: [],
          _pending: true,
        }
        messages.value.push(pendingMsg)
        pendingLimit.value = { message: event.message, convId: event.conv_id, kind: event.kind, messageId: event.message_id }
        break
      } else if (event.type === 'done') {
        if (event.stopped) {
          proxyMsg.content = (proxyMsg.content || '') + '\n\n' + t('chat.userStoppedNote')
        } else {
          proxyMsg.content = streamedText
        }
        if (typeof event.prompt_tokens === 'number') {
          contextTokens.value = event.prompt_tokens
          proxyMsg.token_count = event.prompt_tokens
        }
        proxyMsg._pending = false
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
  // While suspended, if the user enters a new question: force the same conv_id, and the backend treats it as "stop" and discards the suspension;
  // the suspension hint message stays in the conversation history (as history); only the local pending flag is cleared
  if (pendingLimit.value) {
    conversationId.value = pendingLimit.value.convId
    const pm = messages.value.find(m => m.id === pendingLimit.value!.messageId)
    if (pm) pm._pending = false
  }
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

// Resume from suspension: continue (replay the rejected call after adding quota) / stop (answer with the already accumulated results)
async function resumeRun(action: 'continue' | 'stop') {
  const pl = pendingLimit.value
  if (!pl || isStreaming.value) return
  const convId = pl.convId
  const msgId = pl.messageId
  pendingLimit.value = null
  conversationId.value = convId
  // Reuse the message the backend saved at suspension: clear its content and accept streamed tokens; the backend replaces it in place when done
  const msg = messages.value.find(m => m.id === msgId)
  if (!msg) return
  // Stop: keep the original suspension hint copy, and let the frontend overlay a termination notice in the current language when done;
  // Continue: clear the content and accept the newly streamed answer
  if (action === 'continue') {
    msg.content = ''
    msg.citations = []
    msg.agentSteps = []
  }
  msg._pending = false
  const proxyMsg = msg
  isStreaming.value = true
  isPinnedToBottom.value = true
  await scrollToBottom()
  await nextTick()
  doStream('', proxyMsg, msgId, false, action)
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
  contextTokens.value = 0
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
      <!-- Pagination hint: automatically load earlier conversations when scrolling up to the top -->
      <div v-if="totalRounds > 0" class="history-sentinel" aria-live="polite">
        <span v-if="isLoadingOlder" class="history-sentinel-spinner" aria-hidden="true"></span>
        <span v-if="isLoadingOlder">{{ t('chat.loadingOlder') }}</span>
        <span v-else-if="!hasMoreOlder" class="history-sentinel-done">{{ t('chat.allShown', { count: totalRounds }) }}</span>
      </div>
      <template v-for="msg in messages" :key="msg.id">
        <ChatMessage
          v-if="!msg._pending"
          :message="msg"
          :is-streaming="isStreaming && msg.role === 'assistant' && msg === messages[messages.length - 1]"
          :queue-position="queuePosition"
          :search-keyword="searchKw"
          :active-match="msg.id === activeMatchId"
          @regenerate="regenerateAnswer"
        />
      </template>

      <!-- Inline suspension bubble (shown when the limit is hit, blends into the message stream) -->
      <div v-if="pendingLimit" :key="pendingLimit.convId + ':' + pendingLimit.message" class="resume-inline-bubble" role="alert">
        <div class="resume-bubble-icon">⏸️</div>
        <div class="resume-bubble-body">
          <div class="resume-bubble-msg">{{ pendingLimit.message }}</div>
          <div class="resume-bubble-actions">
            <NButton type="primary" :loading="isStreaming" @click="continueResume">
              {{ t('chat.continueResume') }}
            </NButton>
            <NButton :disabled="isStreaming" @click="stopResume">
              {{ t('chat.stopResume') }}
            </NButton>
          </div>
          <div class="resume-bubble-hint">{{ t('chat.resumeHint') }}</div>
        </div>
      </div>
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

    <!-- Floating search bar: search loaded conversation history -->
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

    <!-- Modal: history questions of the current conversation -->
    <AppModal v-model:show="showQuestionsModal" :title="t('chat.historyQuestionsTitle')" size="wide">
      <div class="questions-list" ref="questionsListRef">
        <NEmpty v-if="!questionsLoading && questions.length === 0" :description="t('chat.noQuestions')" style="padding:24px 0" />
        <div
          v-for="q in questions"
          :key="q.id"
          class="question-row"
          role="button"
          tabindex="0"
          @click="onQuestionClick(q.id)"
          @keydown.enter.prevent="onQuestionClick(q.id)"
          @keydown.space.prevent="onQuestionClick(q.id)"
        >
          <div class="question-row-index">Q</div>
          <div class="question-row-body">
            <div class="question-row-text">{{ q.content }}</div>
            <div class="question-row-meta" v-if="q.created_at">
              {{ new Intl.DateTimeFormat(currentLocale, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(q.created_at)) }}
            </div>
          </div>
        </div>
      </div>
      <AppPagination
        v-if="!questionsLoading"
        :page="questionsPage"
        :page-size="QUESTIONS_PAGE_SIZE"
        :item-count="questionsTotalRounds"
        simple
        align="center"
        @update:page="onQuestionsPageChange"
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
        <NButton size="tiny" ghost class="search-trigger-btn" @click="openQuestionsModal">
          <template #icon><NIcon size="14"><List /></NIcon></template>
          {{ t('chat.historyQuestions') }}
        </NButton>
        <div
          v-if="contextTokens > 0"
          class="context-meter"
          :class="contextRatioClass"
          :title="t('chat.contextTokensTip')"
        >
          <span class="context-meter-text">{{ t('chat.contextTokens', { used: formatTokens(contextTokens), total: formatTokens(auth.contextWindow) }) }}</span>
          <span class="context-meter-bar"><span class="context-meter-fill" :style="{ width: contextRatioPct + '%' }"></span></span>
        </div>
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
/* KB preview panel is widened on its own to fit a 3-column grid (consistent with KbPickerModal) without affecting the chat panel */
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

/* ── Optimized conversation history list (the "select a conversation" panel in the middle) ── */
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

/* Modal scrollable list (shared by "More conversations" and "More knowledge bases") */
.picker-scroll { max-height: 60vh; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }

/* History questions modal list */
.questions-list {
  max-height: 56vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-right: 4px;
}
.question-row {
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
.question-row:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.question-row:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.question-row-index {
  width: 28px;
  height: 28px;
  border-radius: var(--radius);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}
.question-row-body {
  flex: 1;
  min-width: 0;
}
.question-row-text {
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.question-row-meta {
  margin-top: 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
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
/* ── Skill picker modal (same card style as SkillsView .sk-card, 3-column grid) ── */
/* Card chrome unified with .sk-card (driven by light/dark tokens) */
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
/* Selected state: consistent with .sk-card's hover/focus — primary-color border + soft primary-color fill */
.skill-pick-card.active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}
/* 3-column grid: equal widths, 3 per row */
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
/* KB / skill picker buttons above the input box: font size bumped slightly from tiny to 13px for better readability */
.skill-selector-bar :deep(.n-button) {
  font-size: 13px;
}
.chat-input-area {
  display: flex;
  gap: var(--space-2);
  padding: 0 0 var(--space-3);
  flex-shrink: 0;
}

/* ── Inline suspension bubble (shown when the limit is hit, blends into the message stream) ── */
.resume-inline-bubble {
  display: flex;
  gap: 10px;
  margin: var(--space-3) 0;
  padding: var(--space-3) var(--space-4);
  border-radius: 14px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--color-primary, #4098fc) 14%, var(--color-card-bg)),
    var(--color-card-bg)
  );
  border: 1px solid color-mix(in srgb, var(--color-primary, #4098fc) 38%, transparent);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  align-items: flex-start;
  /* Entrance: fade-in + slide-up plus one highlight pulse */
  animation:
    resume-bubble-in 0.32s ease-out both,
    resume-bubble-pulse 1.1s ease-out 0.2s 1;
}
@keyframes resume-bubble-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes resume-bubble-pulse {
  0% {
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08),
                0 0 0 0 color-mix(in srgb, var(--color-primary, #4098fc) 55%, transparent);
  }
  60% {
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08),
                0 0 0 8px color-mix(in srgb, var(--color-primary, #4098fc) 0%, transparent);
  }
  100% {
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08),
                0 0 0 0 transparent;
  }
}
@media (prefers-reduced-motion: reduce) {
  .resume-inline-bubble { animation: resume-bubble-in 0.2s ease-out both; }
}
.resume-bubble-icon {
  font-size: 20px;
  line-height: 1.4;
  flex-shrink: 0;
}
.resume-bubble-body {
  flex: 1;
  min-width: 0;
}
.resume-bubble-msg {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  margin-bottom: 8px;
  white-space: pre-wrap;
}
.resume-bubble-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.resume-bubble-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-top: 6px;
}


.chat-input-area :deep(.n-input) {
  flex: 1;
}

/* ── LLM context token meter (right side of the skill bar above the input) ── */
.context-meter {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 8px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  font-variant-numeric: tabular-nums;
  max-width: 220px;
}
.context-meter-text {
  font-size: 11px;
  color: var(--color-text-muted);
  white-space: nowrap;
}
.context-meter-bar {
  width: 120px;
  height: 4px;
  border-radius: 9999px;
  background: var(--color-border);
  overflow: hidden;
}
.context-meter-fill {
  display: block;
  height: 100%;
  border-radius: 9999px;
  background: var(--color-primary);
  transition: width .3s ease;
}
.context-meter.ok .context-meter-fill { background: var(--color-primary); }
.context-meter.warn .context-meter-fill { background: #f0a020; }
.context-meter.danger .context-meter-fill { background: #e0413e; }
.context-meter.warn .context-meter-text { color: #f0a020; }
.context-meter.danger .context-meter-text { color: #e0413e; }

/* ── Scroll-to-bottom button ── */
.scroll-bottom-btn {
  position: absolute;
  bottom: 104px;
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

/* ── Floating search bar (search conversation history) ── */
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
    bottom: 98px;
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

/* ── Conversation pagination hint (load earlier at top / reached the top) ── */
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
