<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { currentLocale } from '@/i18n/useLocale'
import { NInput, NButton, NIcon, NTag, NCard, NEmpty, NSpace, NSpin, NSwitch, NTooltip, useMessage, useDialog } from 'naive-ui'
import KbPickerModal from '@/components/kb/KbPickerModal.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppCard from '@/components/common/AppCard.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import { Send, StopCircle, Chatbubbles, List, Add, ChevronDown, Sparkles, Search, Close, FolderOpen, Folder, Create, DocumentText, CloudUploadOutline } from '@vicons/ionicons5'
import ChatMessage from '@/components/chat/ChatMessage.vue'
import { streamChat, getConversation, getConversationMessages, getPendingLimit, listConversations, updateConversationSummary, compactConversation } from '@/api/chat'
import type { ConversationSummaryState } from '@/api/chat'
import { listWorkspace, mkdirWorkspace, uploadWorkspace, fileToBase64 } from '@/api/workspace'
import type { WorkspaceEntry } from '@/api/workspace'
import { useAuthStore } from '@/stores/auth'
import { useChatUnreadStore } from '@/stores/chatUnread'
import { listKnowledgeBases } from '@/api/documents'
import { listSkills } from '@/api/skills'
import { renderStreamingHtml } from '@/utils/think'
import { backendErrorMessage } from '@/utils/backendError'
import type { ChatMessage as ChatMsg, Skill } from '@/types'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const auth = useAuthStore()
const chatUnread = useChatUnreadStore()
const nmessage = useMessage()
const dialog = useDialog()

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

// ── Workspace directory selector (v2: user workspace == REPL tool dir) ──
// workspaceDir: '' = user workspace ROOT (= default). Non-empty = chosen sub-dir.
const workspaceDir = ref('')
const showWsModal = ref(false)
const wsCurrentPath = ref('')
const wsDirs = ref<WorkspaceEntry[]>([])
const wsLoading = ref(false)
const wsNewName = ref('')
const wsCreating = ref(false)

// ── File picker modal: insert a [[file:rel_path]] reference into the input ──
// Reuses the workspace listing API but shows files AND directories; clicking a
// directory navigates, clicking a file selects it (its rel_path is inserted).
const showFileModal = ref(false)
const fpPath = ref('')
const fpEntries = ref<WorkspaceEntry[]>([])
const fpLoading = ref(false)
// ref to the chat textarea so we can insert at the caret position
const inputRef = ref<any>(null)

const fpCrumbSegments = computed(() => {
  if (!fpPath.value) return []
  const parts = fpPath.value.split('/').filter(Boolean)
  return parts.map((name, i) => ({
    name,
    path: parts.slice(0, i + 1).join('/'),
  }))
})

async function fpLoadDirs() {
  fpLoading.value = true
  try {
    const data = await listWorkspace(fpPath.value)
    // Sort: directories first, then files; alphabetical within each group.
    fpEntries.value = (data.entries || []).slice().sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e.message) || t('workspace.errors.load'))
    fpEntries.value = []
  } finally {
    fpLoading.value = false
  }
}

async function openFileModal() {
  fpPath.value = ''
  await fpLoadDirs()
  showFileModal.value = true
}

function fpEnterDir(entry: WorkspaceEntry) {
  fpPath.value = entry.rel_path
  fpLoadDirs()
}

function fpCrumb(path: string) {
  fpPath.value = path
  fpLoadDirs()
}

function insertAtCursor(token: string) {
  // NInput exposes `textareaElRef` / `inputElRef` (Refs to the native elements).
  const el = inputRef.value?.textareaElRef?.value || inputRef.value?.inputElRef?.value
  const cur = inputText.value
  if (el && typeof el.selectionStart === 'number') {
    const start = el.selectionStart
    const end = el.selectionEnd
    inputText.value = cur.slice(0, start) + token + cur.slice(end)
    nextTick(() => {
      const pos = start + token.length
      el.focus()
      el.setSelectionRange(pos, pos)
    })
  } else {
    const sep = cur && !/\s$/.test(cur) ? ' ' : ''
    inputText.value = cur + sep + token + ' '
    nextTick(() => el?.focus())
  }
}

function fpSelectFile(entry: WorkspaceEntry) {
  if (entry.type !== 'file') return
  insertAtCursor(`[[file:${entry.rel_path}]]`)
  showFileModal.value = false
}

// ── Drag-and-drop upload into the file-picker modal ──
// A file dropped anywhere inside the modal is uploaded to the directory
// currently displayed (root when fpPath is ''), then treated exactly like a
// file selection: the modal closes and the path is inserted into the input.
const fpDragging = ref(false)
const fpDragDepth = ref(0)
const fpUploading = ref(false)

function fpOnDragEnter(e: DragEvent) {
  e.preventDefault()
  fpDragDepth.value++
  fpDragging.value = true
}
function fpOnDragLeave(e: DragEvent) {
  e.preventDefault()
  fpDragDepth.value = Math.max(0, fpDragDepth.value - 1)
  if (fpDragDepth.value === 0) fpDragging.value = false
}
function fpOnDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}
function fpOnDrop(e: DragEvent) {
  e.preventDefault()
  fpDragDepth.value = 0
  fpDragging.value = false
  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return
  void fpUploadDropped(Array.from(files))
}

async function fpUploadDropped(files: File[]) {
  if (fpUploading.value) return

  // Detect name collisions with files already present in the directory the
  // modal is currently showing. The backend `upload` action overwrites
  // existing files, so we ask before doing that.
  const existing = files.filter(f =>
    fpEntries.value.some(e => e.type === 'file' && e.name === f.name),
  )

  let toUpload = files
  if (existing.length > 0) {
    const overwrite = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: t('workspace.overwriteTitle'),
        content:
          existing.length === 1
            ? t('workspace.overwriteConfirmOne', { name: existing[0].name })
            : t('workspace.overwriteConfirmMany', { count: existing.length }),
        positiveText: t('workspace.overwrite'),
        negativeText: t('workspace.skipExisting'),
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
      })
    })
    if (!overwrite) {
      toUpload = files.filter(f => !existing.includes(f))
      if (toUpload.length === 0) {
        nmessage.info(t('workspace.allSkipped'))
        return
      }
    }
  }

  await fpDoUpload(toUpload)
}

async function fpDoUpload(files: File[]) {
  fpUploading.value = true
  const ok: string[] = []
  try {
    for (const file of files) {
      // Target the directory the modal is currently showing (root if fpPath is '').
      const name = fpPath.value ? `${fpPath.value}/${file.name}` : file.name
      try {
        const content = await fileToBase64(file)
        await uploadWorkspace(name, content)
        ok.push(name)
      } catch (err: any) {
        nmessage.error(`${file.name}: ${err?.message || t('workspace.errors.upload')}`)
      }
    }
  } finally {
    fpUploading.value = false
  }
  if (ok.length) {
    const token = ok.map(p => `[[file:${p}]]`).join(' ')
    insertAtCursor(token)
    showFileModal.value = false
  }
}


// Breadcrumb segments derived from the current modal path (root + each nested part).
const wsCrumbSegments = computed(() => {
  if (!wsCurrentPath.value) return []
  const parts = wsCurrentPath.value.split('/').filter(Boolean)
  return parts.map((name, i) => ({
    name,
    path: parts.slice(0, i + 1).join('/'),
  }))
})

async function wsLoadDirs() {
  wsLoading.value = true
  try {
    const data = await listWorkspace(wsCurrentPath.value)
    // Only show immediate subdirectories in the selector.
    wsDirs.value = (data.entries || []).filter(e => e.type === 'dir')
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e.message) || t('workspace.errors.load'))
    wsDirs.value = []
  } finally {
    wsLoading.value = false
  }
}

async function openWsModal() {
  wsCurrentPath.value = workspaceDir.value
  wsNewName.value = ''
  wsCreating.value = false
  await wsLoadDirs()
  showWsModal.value = true
}

function wsEnterDir(entry: WorkspaceEntry) {
  wsCurrentPath.value = entry.rel_path
  wsLoadDirs()
}

function wsCrumb(path: string) {
  wsCurrentPath.value = path
  wsLoadDirs()
}

function wsToggleCreate() {
  wsCreating.value = !wsCreating.value
}

async function wsCreateDir() {
  const name = wsNewName.value.trim()
  if (!name) return
  const full = wsCurrentPath.value ? `${wsCurrentPath.value}/${name}` : name
  try {
    await mkdirWorkspace(full)
    wsNewName.value = ''
    wsCreating.value = false
    wsCurrentPath.value = full
    await wsLoadDirs()
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e.message) || t('workspace.errors.create'))
  }
}

function wsConfirmDir() {
  workspaceDir.value = wsCurrentPath.value
  showWsModal.value = false
}

const queuePosition = ref<number | null>(null)
// Tracks the backend agent_step stage during streaming so the assistant bubble can show "检索中" vs "生成中".
const assistantStage = ref<string | null>(null)
// LLM context token count: total tokens of the latest request body (system prompt + history + RAG + memory + tools + question)
const contextTokens = ref(0)
// Split of that same submission. `persistent` = compressed summary + verbatim
// history (the only part manual compaction can shrink); `transient` = system
// prefix + RAG + memory + tool records + the current question.
const persistentTokens = ref(0)
const transientTokens = ref(0)
// Summary-folding cursor: how many of the oldest messages are already
// represented by the summary, out of the conversation total.
const summaryMsgCount = ref(0)
const totalMessages = ref(0)
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
  // Track the current offset so we can restore it when returning from another page.
  savedScrollTop.value = el.scrollTop
  // Scroll up to the top → automatically load earlier conversations (previous page)
  if (el.scrollTop <= 48 && hasMoreOlder.value && !isLoadingOlder.value) {
    loadOlder()
  }
}

// Load earlier conversations forward: request the previous page from the server, prepend it to the list, and compensate the scroll position to avoid jumps
// A manually terminated round in history (status==='stopped'): the DB stores only the original hint copy,
// and at load time overlays a localized termination notice based on the current UI language, so it still shows after refresh.
function applyStoppedNote(msgs: ChatMsg[]): ChatMsg[] {
  return msgs.map(m => {
    // Map the backend's snake_case agent_steps into the frontend AgentStep shape
    // so the persisted processing trace replays after refresh / reopen (the
    // ChatMessage component already renders message.agentSteps).
    const steps = (m as any).agent_steps || m.agentSteps || []
    const base = { ...m, agentSteps: steps }
    if (m.status === 'stopped') {
      // A manually terminated turn: the DB keeps the original hint copy and we
      // overlay a localized termination note based on the current UI language.
      return { ...base, content: (m.content || '') + '\n\n' + t('chat.userStoppedNote') }
    }
    if (m.status === 'error') {
      // A failed generation: show whatever partial text we captured (if any)
      // plus a localized failure note so the turn is never silently blank after
      // a page refresh / reopen. The agent steps above are replayed too.
      return { ...base, content: (m.content || '') + '\n\n' + t('chat.generationFailedNote') }
    }
    return base
  })
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

// Restore the context token count from loaded messages (take the last assistant message that has token_count).
// The persistent/transient split is NOT persisted (it describes one submission,
// not the message), so it is cleared here and the modal hides that row until
// the next live turn reports it.
function syncContextFromMessages() {
  persistentTokens.value = 0
  transientTokens.value = 0
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
const emptyMode = ref<'kb' | ''>('')
const showMoreConv = ref(false)
const showMoreKb = ref(false)

// ── Per-conversation selections (workspace dir + KB + skill) ──
// Persisted in localStorage keyed by conversation id, so opening a conversation
// from history restores exactly the workspace directory, knowledge base and
// skill that were used with it.
type ConvSettings = { kbId?: string; skillId?: string | null; workspaceDir?: string }
const CONV_SETTINGS_KEY = 'ragclaw:conv-settings'
const convSettingsMap = ref<Record<string, ConvSettings>>({})

function saveConvSettings() {
  localStorage.setItem(CONV_SETTINGS_KEY, JSON.stringify(convSettingsMap.value))
}

// Persist the currently selected workspace dir / KB / skill under a conversation id.
function persistConvSettings(convId: string) {
  convSettingsMap.value[convId] = {
    kbId: selectedKbId.value || '',
    skillId: selectedSkillId.value ?? null,
    workspaceDir: workspaceDir.value || '',
  }
  saveConvSettings()
}

// Restore the workspace dir / KB / skill saved for a conversation (validating
// that referenced KB / skill still exist so we never point at a deleted item).
function restoreConvSettings(convId: string) {
  const saved = convSettingsMap.value[convId]
  if (!saved) return
  if (saved.kbId != null) {
    selectedKbId.value = (saved.kbId && kbs.value.find(k => k.id === saved.kbId)) ? saved.kbId : ''
  }
  if (saved.skillId !== undefined) {
    selectedSkillId.value = (saved.skillId && skills.value.find(s => s.id === saved.skillId)) ? saved.skillId : null
  }
  if (saved.workspaceDir != null) {
    workspaceDir.value = saved.workspaceDir
  }
}

// ── Conversation history modal pagination ──
const convPage = ref(1)
const convPageSize = 8
const pagedConversations = computed(() => {
  const start = (convPage.value - 1) * convPageSize
  return conversations.value.slice(start, start + convPageSize)
})
const convTotalPages = computed(() => Math.max(1, Math.ceil(conversations.value.length / convPageSize)))
watch(showMoreConv, (v) => { if (v) { convPage.value = 1; loadConversations() } })

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

// ── Context inspector modal (click the meter) ──
// Shows the persistent/transient split of the last submission, the summary
// paragraphs, and the two manual actions (edit summary / compact).
const showContextModal = ref(false)
const ctxSummaryText = ref('')
const ctxDraft = ref('')
const ctxEditing = ref(false)
const ctxLoading = ref(false)
const ctxBusy = ref(false)

// Shares of the WINDOW (not of each other), so the two numbers add up to the
// same percentage the meter shows.
const contextPersistentPct = computed(() => {
  const w = auth.contextWindow || 1
  return Math.round(Math.min(1, persistentTokens.value / w) * 100)
})
const contextTransientPct = computed(() => {
  const w = auth.contextWindow || 1
  return Math.round(Math.min(1, transientTokens.value / w) * 100)
})
const hasBreakdown = computed(() => persistentTokens.value + transientTokens.value > 0)

// The summary is a single TEXT column whose paragraphs are joined with "\n"
// (one paragraph per fold). Split it back for display; there is deliberately no
// per-paragraph "which messages did this cover" mapping.
const ctxSummaryParagraphs = computed(() =>
  ctxSummaryText.value.split('\n').map(s => s.trim()).filter(Boolean),
)
const ctxDirty = computed(() => ctxDraft.value.trim() !== ctxSummaryText.value.trim())

function applySummaryState(s: ConversationSummaryState | { summary_text?: string; summary_msg_count?: number; total_messages?: number }) {
  ctxSummaryText.value = s.summary_text || ''
  ctxDraft.value = ctxSummaryText.value
  summaryMsgCount.value = s.summary_msg_count || 0
  totalMessages.value = s.total_messages || 0
}

async function openContextModal() {
  if (!conversationId.value) return
  showContextModal.value = true
  ctxEditing.value = false
  ctxLoading.value = true
  try {
    // include_messages=false keeps this cheap: we only need the summary state,
    // which rides on the existing conversation-detail endpoint.
    const detail = await getConversation(conversationId.value, false)
    applySummaryState(detail)
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e?.message) || t('chat.contextModal.loadFailed'))
  } finally {
    ctxLoading.value = false
  }
}

function guardContextAction(): boolean {
  if (isStreaming.value) {
    nmessage.warning(t('chat.contextModal.streamingDisabled'))
    return false
  }
  return !!conversationId.value && !isReadonly.value
}

async function persistSummary(text: string) {
  ctxBusy.value = true
  try {
    applySummaryState(await updateConversationSummary(conversationId.value!, text))
    ctxEditing.value = false
    nmessage.success(t('chat.contextModal.saved'))
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e?.message) || t('chat.contextModal.loadFailed'))
  } finally {
    ctxBusy.value = false
  }
}

function saveSummaryEdit() {
  if (!guardContextAction()) return
  const text = ctxDraft.value.trim()
  // Clearing the text while the cursor is advanced permanently hides
  // history[:cursor] from the model (the cursor is intentionally immutable),
  // so this destructive case needs an explicit second confirmation.
  if (!text && summaryMsgCount.value > 0) {
    dialog.warning({
      title: t('chat.contextModal.clearConfirmTitle'),
      content: t('chat.contextModal.clearConfirmBody', { count: summaryMsgCount.value }),
      positiveText: t('chat.contextModal.clearConfirmOk'),
      negativeText: t('chat.contextModal.cancel'),
      onPositiveClick: () => { persistSummary('') },
    })
    return
  }
  persistSummary(text)
}

async function runCompact() {
  if (!guardContextAction()) return
  ctxBusy.value = true
  try {
    const state = await compactConversation(conversationId.value!, 0.5)
    applySummaryState(state)
    ctxEditing.value = false
    nmessage.success(t('chat.contextModal.compacted', {
      done: state.summary_msg_count,
      total: state.total_messages,
    }))
  } catch (e: any) {
    nmessage.error(backendErrorMessage(e?.message) || t('chat.contextModal.loadFailed'))
  } finally {
    ctxBusy.value = false
  }
}


const showPicker = computed(() => emptyMode.value !== '' && messages.value.length === 0 && !conversationId.value)

const selectedKb = computed(() => kbs.value.find((k: any) => k.id === selectedKbId.value))
const currentKbName = computed(() => selectedKb.value?.name || t('chat.noKb'))

function selectAndClose(convId: string) {
  emptyMode.value = ''
  showMoreConv.value = false
  chatUnread.clearConversation(convId)
  // If we're already viewing this conversation, the route param won't change so the
  // watcher won't re-fire — just close the modal. The mounted instance already
  // preserves all state (messages, draft, scroll), so there is nothing to reload.
  // Otherwise push the route; the watcher picks it up and loads it exactly once.
  if ((route.params.id as string | undefined) === convId) return
  router.push(`/chat/${convId}`)
}

function onKbPick(id: string | null) {
  selectedKbId.value = id ?? ''
  showMoreKb.value = false
}

async function loadConversations() {
  try {
    conversations.value = await listConversations()
  } catch { conversations.value = [] }
}

onMounted(async () => {
  isReadonly.value = false

  // Refresh LLM config status. The backend loads the .env API key during async
  // startup, so the initial health check (run at app boot) may report the key as
  // not configured yet. Poll briefly so the input self-enables without an F5.
  auth.refreshLlmStatus()

  // Load persisted per-conversation selections (workspace dir + KB + skill).
  // Migrate the legacy KB-only map (ragclaw:conv-kb-map) if present.
  try {
    const stored = localStorage.getItem(CONV_SETTINGS_KEY)
    if (stored) convSettingsMap.value = JSON.parse(stored)
    const legacyKb = localStorage.getItem('ragclaw:conv-kb-map')
    if (legacyKb) {
      const kbMap: Record<string, string> = JSON.parse(legacyKb)
      let migrated = false
      for (const [cid, kbId] of Object.entries(kbMap)) {
        if (!convSettingsMap.value[cid]) {
          convSettingsMap.value[cid] = { kbId }
          migrated = true
        } else if (convSettingsMap.value[cid].kbId == null) {
          convSettingsMap.value[cid].kbId = kbId
          migrated = true
        }
      }
      if (migrated) saveConvSettings()
    }
  } catch { /* ignore */ }

  try {
    const res = await listKnowledgeBases()
    kbs.value = res.data
    const kbFromQuery = route.query.kb as string | undefined
    if (kbFromQuery && kbs.value.find(k => k.id === kbFromQuery)) {
      selectedKbId.value = kbFromQuery
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
  } else if (chatUnread.hasUnread && chatUnread.lastConversationId) {
    // An answer finished streaming while the user was away: open it now and
    // clear the unread flag so the sidebar dot disappears.
    const pendingId = chatUnread.lastConversationId
    chatUnread.clearUnread()
    await loadConversation(pendingId)
  } else {
    // No id and nothing unread: restore the last opened conversation so that
    // leaving and re-entering the chat page preserves its state.
    const lastConv = localStorage.getItem('ragclaw:last-conv')
    if (lastConv) await loadConversation(lastConv)
  }
})

onUnmounted(() => {
  stopFollowTimer()
  // NOTE: we intentionally do NOT abort the in-flight stream here. The backend
  // keeps generating server-side and persists incrementally, so letting the
  // background stream finish lets the unread/red-dot signal fire for a turn that
  // completes while the user is on another page. Returning to a still-generating
  // conversation is handled by followInProgress() (progressive server replay).
})

// ── State retention across navigation ──
// AppLayout keeps this ChatView instance mounted for the whole session and only
// toggles its visibility with `visibility:hidden` (NEVER display:none) when the user
// navigates to another page — so the DOM node stays laid out and the browser
// preserves the message list's scrollTop, the draft input, and all in-component
// state naturally. We only (re)load when the conversation id actually changes.
const isActive = ref(true)
// Mirror of the message list scrollTop, continuously updated on scroll (and captured
// explicitly when leaving the page), used to re-apply the position when returning to
// the conversation (belt-and-suspenders in case any browser resets it on toggle).
const savedScrollTop = ref(0)

// Single entry point for switching which conversation is shown. The draft input is
// NEVER cleared here — inputText is only reset on an actual send — so a half-typed
// message survives every navigation (switch page, open history modal, etc.).
async function openConversation(targetId: string | null | undefined) {
  if (!targetId) {
    // No explicit target (e.g. route /chat with no id, or the id flipping to
    // undefined while navigating AWAY from the chat page). We MUST NOT reset here:
    // the mounted instance already holds the user's state (messages, scroll, draft),
    // and the sidebar restores the last/finished conversation via last-conv. A true
    // "new conversation" is only triggered by the explicit New Conversation button
    // (newConversation()) — never by merely navigating to /chat. So just preserve
    // whatever is currently shown.
    return
  }
  if (targetId === conversationId.value) return // already shown — preserve state, no reload
  await loadConversation(targetId)
}

// Persist the current workspace dir / KB / skill whenever any of them changes,
// as long as we are inside an existing conversation with messages.
watch([selectedKbId, selectedSkillId, workspaceDir], () => {
  if (conversationId.value && messages.value.length > 0) {
    persistConvSettings(conversationId.value)
  }
})

// Drive loading + visibility from the full route path. Because ChatView is kept
// mounted (visibility toggle, never detached), this fires on every navigation. When
// the user leaves the chat page we just mark inactive and keep all state; when they
// return to the SAME conversation we preserve everything (scroll, draft, messages)
// and only re-apply the saved scroll offset; when they switch to a DIFFERENT
// conversation we load it.
watch(() => route.fullPath, async () => {
  if (!route.meta.keepAlive) {
    // Navigated to a non-chat page. Capture the current scroll offset explicitly
    // (definitive, not relying on the last onScroll event) and keep the instance
    // alive but inactive. The DOM node is hidden via visibility (not display), so the
    // browser preserves the scroll position, draft and all in-component state.
    const el = messagesContainer.value
    if (el) savedScrollTop.value = el.scrollTop
    isActive.value = false
    return
  }
  isActive.value = true
  const id = route.params.id as string | undefined
  if (id !== conversationId.value) {
    await openConversation(id)
  } else {
    // Same conversation we left: the DOM (and its scrollTop) was never detached, so the
    // browser already preserved the offset. Re-apply it after the next paint as a safety
    // net in case anything reset it.
    const target = savedScrollTop.value
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const el = messagesContainer.value
      if (el) el.scrollTop = target
    }))
  }
})

// ── Follow an in-progress generation after navigating back ──
// If the user switches away mid-stream and returns, the live SSE connection from
// the previous component instance is gone, so the view would otherwise freeze on a
// stale snapshot. The backend keeps generating in the background and persists
// incrementally (status "generating" → "complete"), so we replay server state on a
// short timer until the turn reaches a terminal status.
let followTimer: ReturnType<typeof setInterval> | null = null

function stopFollowTimer() {
  if (followTimer !== null) {
    clearInterval(followTimer)
    followTimer = null
  }
}

async function followInProgress(convId: string) {
  stopFollowTimer()
  // Safety cap so a follow-poll can never run forever (e.g. if the backend
  // message status never flips to a terminal value). ~48s is far longer than
  // any legitimate generation; after that we force a clean load.
  let polls = 0
  const MAX_POLLS = 40
  followTimer = setInterval(async () => {
    try {
      polls++
      const data = await getConversationMessages(convId, 'last', PAGE_SIZE_ROUNDS)
      const msgs = applyStoppedNote(data.messages)
      const last = msgs[msgs.length - 1]
      if (!last || last.role !== 'assistant') {
        stopFollowTimer()
        return
      }
      if (last.status && last.status !== 'generating') {
        // Turn finished: do a clean full reload (restores scroll + final state) and stop.
        stopFollowTimer()
        await loadInitialPage(convId)
        return
      }
      if (polls >= MAX_POLLS) {
        // Safety net: stop polling and do a clean load so the conversation always
        // becomes visible even if the backend status never flips to terminal.
        stopFollowTimer()
        await loadInitialPage(convId)
        return
      }
      // Still generating: patch the in-progress message in place (no scroll fight).
      const idx = messages.value.findIndex((m) => m.id === last.id)
      if (idx !== -1) messages.value[idx] = last
      else messages.value = msgs
      // Keep the stage hint alive by mirroring the latest step message.
      const steps = (last as any).agentSteps || []
      const lastStep = steps[steps.length - 1]
      if (lastStep?.message) assistantStage.value = lastStep.message
    } catch {
      // transient network blip — keep polling (within the MAX_POLLS cap)
    }
  }, 1200)
}

async function loadConversation(id: string) {
  stopFollowTimer()
  try {
    // Fetch only conversation metadata (no messages); messages are loaded via the server-side pagination API
    const conv = await getConversation(id, false)
    conversationId.value = id
    chatUnread.clearConversation(id)
    // Remember the currently open conversation so returning to the chat page
    // from another page restores it (state is preserved across navigation).
    localStorage.setItem('ragclaw:last-conv', id)
    // Restore the workspace dir / KB / skill that were used with this conversation
    restoreConvSettings(id)
    checkReadonly((conv as any).user_id)
    if (isReadonly.value) {
      router.replace({ path: `/chat/${id}`, query: { view_user: (conv as any).user_id } })
    } else {
      router.replace(`/chat/${id}`)
    }
    // Server-side pagination: load the newest page (last page) and scroll to the bottom
    await loadInitialPage(id)
    // Only follow an in-progress generation if THIS client is actively streaming
    // this conversation (the user switched away mid-stream and came back). Never
    // infer "generating" from the DB message status: cache-hit answers are
    // persisted with status="generating" yet already finished, and relying on the
    // status would start a follow-poll that never resolves (red dot + blank view).
    if (isStreaming.value) {
      followInProgress(id)
    }
    // Restore suspension state after refresh: if a pending quota suspension awaiting
    // confirmation exists, rebuild the inline bubble. Best-effort only — a failure
    // here must not wipe the conversation we just loaded.
    try {
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
      /* ignore — suspension UI is optional */
    }
  } catch (e) {
    console.error('[ChatView] loadConversation failed for', id, e)
    nmessage.error(t('chat.loadConversationFailed', { msg: backendErrorMessage((e as any)?.message) }))
    // Keep the URL on the intended conversation. Do NOT silently bounce to /chat
    // (a brand-new conversation) — that hides the failure and strands the user.
    conversationId.value = id
    messages.value = []
    currentPage.value = 1
    totalPages.value = 1
    totalRounds.value = 0
  }
}

async function doStream(query: string, proxyMsg: ChatMsg, userMsgId: string, skipCache = false, resumeAction: 'continue' | 'stop' | null = null, workspaceDir?: string) {
  stopFollowTimer()  // a live stream takes over; cancel any in-progress follow poll
  // Selections captured at stream start (so they stay correct even if the user
  // switches to another conversation while this one is still streaming).
  const streamKbId = selectedKbId.value
  const streamSkillId = selectedSkillId.value
  const streamWorkspaceDir = workspaceDir || ''
  const aid = proxyMsg.id
  let streamedText = ''
  queuePosition.value = null
  // Start with the retrieval status; as agent_step events arrive we reflect the REAL stage message
  // (routing / retrieval / skill / tool / generating) in the bubble so every phase is shown honestly.
  assistantStage.value = t('chat.retrieving')
  abortCtl = new AbortController()
  try {
    for await (const event of streamChat(query, selectedKbId.value, conversationId.value, selectedSkillId.value || undefined, abortCtl.signal, skipCache, resumeAction, workspaceDir, Intl.DateTimeFormat().resolvedOptions().timeZone)) {
      if (event.type === 'queue') {
        queuePosition.value = event.position
      } else if (event.type === 'token') {
        assistantStage.value = null
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
        // Live SSE steps carry link fields (url/filename/path) at the top level,
        // whereas persisted steps nest them under `extra`. Normalize so both
        // shapes are uniform for the download-button UI.
        const ev = event as any
        const normalized = {
          stage: ev.stage,
          message: ev.message,
          extra: ev.extra ?? {
            ...(ev.url ? { url: ev.url } : {}),
            ...(ev.filename ? { filename: ev.filename } : {}),
            ...(ev.path ? { path: ev.path } : {}),
          },
        }
        proxyMsg.agentSteps.push(normalized)
        // Drive the streaming placeholder with the REAL backend stage message so every
        // phase (routing / retrieval / skill_load / tool / generating …) is reflected honestly.
        assistantStage.value = (event as any).message || t('chat.thinking')
      } else if (event.type === 'error') {
        streamedText = t('chat.streamError', { msg: backendErrorMessage(event.message) })
        break
      } else if (event.type === 'need_user_input') {
        // Suspension: the backend has saved an assistant message (the hint copy) and is
        // waiting for the user to choose "continue" or "stop" because the tool-call round
        // quota was hit.
        // Mirror the finished-stream (done) rules:
        //  - If the user is currently looking at THIS conversation, render the suspension
        //    hint bubble inline and surface the continue/stop controls.
        //  - If they have switched to another conversation (still in chat) or to another
        //    page, flag the conversation as unread so a red dot appears on the sidebar Chat
        //    label / history button / conversation row — instead of mutating the view they
        //    are currently looking at. The suspension state is re-derived from the server
        //    when they later open this conversation (see loadConversation → getPendingLimit).
        const currentView = conversationId.value
        const onChat = route.path.startsWith('/chat')
        if (onChat && (currentView === event.conv_id || currentView === undefined)) {
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
        } else {
          chatUnread.markUnread(event.conv_id)
        }
        break
      } else if (event.type === 'context_usage') {
        // One event per LLM submission (each tool round, then the final
        // generation): the latest simply overwrites the previous one.
        contextTokens.value = event.prompt_tokens
        persistentTokens.value = event.persistent_tokens
        transientTokens.value = event.transient_tokens
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
        if (typeof event.persistent_tokens === 'number') persistentTokens.value = event.persistent_tokens
        if (typeof event.transient_tokens === 'number') transientTokens.value = event.transient_tokens
        // Summary-folding cursor: the automatic compressor may have advanced it
        // during this turn, so keep the modal's counters honest without a refetch.
        if (typeof event.summary_msg_count === 'number') summaryMsgCount.value = event.summary_msg_count
        if (typeof event.total_messages === 'number') totalMessages.value = event.total_messages
        proxyMsg._pending = false
        ;(proxyMsg as any)._ttft = event.ttft_ms || 0
        ;(proxyMsg as any)._retrieval = event.retrieval_ms || 0
        ;(proxyMsg as any)._llm = event.llm_ms || 0
        // Persist the workspace dir / KB / skill used for this conversation
        // (captured at stream start) so a brand-new conversation is restorable.
        if (!convSettingsMap.value[event.conversation_id]) {
          convSettingsMap.value[event.conversation_id] = {
            kbId: streamKbId || '',
            skillId: streamSkillId ?? null,
            workspaceDir: streamWorkspaceDir,
          }
          saveConvSettings()
        }
        const currentView = conversationId.value
        const onChat = route.path.startsWith('/chat')
        if (onChat && (currentView === event.conversation_id || currentView === undefined)) {
          // The answer finished for the conversation the user is currently viewing
          // (or a brand-new one just created): keep the URL in sync.
          conversationId.value = event.conversation_id
          router.replace(`/chat/${event.conversation_id}`)
        } else {
          // The answer finished for a DIFFERENT conversation (the user switched to
          // another chat while it was still streaming) or while the user is on
          // another page: flag that conversation as having an unread answer
          // instead of yanking them away.
          chatUnread.markUnread(event.conversation_id)
        }
        window.dispatchEvent(new CustomEvent('ragclaw:conversation-updated'))
      }
    }
    proxyMsg.content = streamedText
  } catch (e: any) {
    if (e?.name !== 'AbortError') {
      // Remove failed user + assistant messages and restore input
      messages.value = messages.value.filter(m => m.id !== userMsgId && m.id !== proxyMsg.id)
      inputText.value = query
      nmessage.error(t('chat.sendFailed', { msg: backendErrorMessage(e.message) }))
    }
  } finally {
    isStreaming.value = false
    queuePosition.value = null
    assistantStage.value = null
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
  doStream(text, proxyMsg, userMsg.id, false, null, workspaceDir.value)
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
  doStream(userMsg.content, proxyMsg, userMsg.id, true, null, workspaceDir.value)
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
  doStream('', proxyMsg, msgId, false, action, workspaceDir.value)
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
  // Starting a fresh conversation clears the remembered open conversation.
  localStorage.removeItem('ragclaw:last-conv')
  loadConversations()
  // Drop any route query and show the bare /chat so the watcher renders the KB picker.
  router.replace('/chat')
}

const isComposing = ref(false)
// Send shortcut mode: 'enter' = Enter sends (Shift+Enter for newline);
// 'shiftEnter' = Shift+Enter sends, plain Enter inserts a newline.
type SendMode = 'enter' | 'shiftEnter'
const SEND_MODE_KEY = 'chat.sendMode'
const sendMode = ref<SendMode>(localStorage.getItem(SEND_MODE_KEY) === 'shiftEnter' ? 'shiftEnter' : 'enter')
watch(sendMode, (v) => localStorage.setItem(SEND_MODE_KEY, v))

function handleKeydown(e: KeyboardEvent) {
  if (isComposing.value) return
  if (e.key !== 'Enter') return
  if (sendMode.value === 'shiftEnter') {
    // Shift+Enter sends; plain Enter falls through to newline.
    if (e.shiftKey) { e.preventDefault(); sendMessage() }
    return
  }
  // 'enter' mode: Enter sends, Shift+Enter inserts a newline.
  if (!e.shiftKey) { e.preventDefault(); sendMessage() }
}
</script>

<template>
  <div class="chat-view">
    <PageHeader :title="t('chat.title')" :icon="Chatbubbles">
      <template #actions>
        <NTag v-if="isReadonly" type="info">{{ t('chat.readonlyMode') }}</NTag>
        <div class="history-btn-wrap">
          <NButton size="small" @click="showMoreConv = true">
            <template #icon><NIcon size="16"><List /></NIcon></template>
            {{ t('chat.history') }}
          </NButton>
          <span v-if="chatUnread.hasUnread" class="history-unread-dot"></span>
        </div>
        <NButton v-if="!isReadonly" size="small" type="primary" @click="newConversation">
          <template #icon><NIcon size="16"><Add /></NIcon></template>
          {{ t('chat.newConversation') }}
        </NButton>
      </template>
    </PageHeader>

    <div class="chat-messages" ref="messagesContainer" @scroll="onScroll" role="log" aria-live="polite" :aria-label="t('chat.ariaMessages')">
      <!-- Centered panel: conversation list preview -->
      <!-- Centered panel: KB list preview -->
      <div v-if="(showPicker && emptyMode === 'kb') || (!showPicker && messages.length === 0 && !conversationId && !selectedKbId)" class="center-panel">
        <div class="center-panel-box" :class="{ 'center-panel-box-wide': emptyMode === 'kb' || (!showPicker && !selectedKbId) }">
          <div class="empty-icon">🧠</div>
          <h3>{{ t('chat.newConversationPickKb') }}</h3>
          <div v-if="kbs.length > 0" class="center-panel-list">
            <AppCard class="kb-pick-card"
              :active="!selectedKbId"
              role="button" tabindex="0"
              @click="selectedKbId = ''"
              @keydown.enter.prevent="selectedKbId = ''"
              @keydown.space.prevent="selectedKbId = ''"
            >
              <div class="kb-pick-inner">
                <div class="kb-pick-avatar kb-pick-avatar-none">🚫</div>
                <div class="kb-pick-body">
                  <strong class="kb-pick-name">{{ t('chat.noKb') }}</strong>
                </div>
              </div>
            </AppCard>
            <AppCard v-for="kb in kbPreview" :key="kb.id" class="kb-pick-card"
              :active="kb.id === selectedKbId"
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
            </AppCard>
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
          :stage-hint="assistantStage"
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
              <span v-if="chatUnread.hasUnreadConversation(c.id)" class="conv-unread-badge">{{ t('chat.hasUnread') }}</span>
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
          <div class="question-row-text">{{ q.content }}</div>
          <div class="question-row-meta" v-if="q.created_at">
            {{ new Intl.DateTimeFormat(currentLocale, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(q.created_at)) }}
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
      :show-none="true"
      :none-label="t('chat.noKb')"
      :none-active="!selectedKbId"
      :sortable="true"
      :page-size="12"
      @select="onKbPick"
      @create="() => router.push('/documents')"
    />

    <AppModal v-model:show="showSkillModal" :title="t('chat.selectSkill')"
      size="wide"
      @after-leave="skillSearchText = ''"
    >
      <NInput v-model:value="skillSearchText" :placeholder="t('chat.searchSkillPlaceholder')" clearable style="margin-bottom:12px" />
      <div class="skill-pick-grid">
        <AppCard class="skill-pick-card"
          :active="!selectedSkillId"
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
        </AppCard>
        <AppCard v-for="s in filteredSkills" :key="s.id" class="skill-pick-card"
          :active="s.id === selectedSkillId"
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
        </AppCard>
      </div>
      <NEmpty v-if="filteredSkills.length === 0" :description="t('chat.noMatchingSkill')" style="padding:16px 0" />
    </AppModal>

    <div v-if="!isReadonly" class="chat-input-wrapper">
      <div class="skill-selector-bar">
        <NButton size="tiny" tertiary class="ws-trigger-btn" @click="openWsModal" :disabled="isStreaming">
          <template #icon><NIcon size="14"><FolderOpen /></NIcon></template>
          {{ t('chat.workspaceDirBtn', { dir: workspaceDir ? workspaceDir : t('workspace.default') }) }}
        </NButton>
        <NButton size="tiny" ghost class="kb-trigger-btn" @click="showMoreKb = true">
          {{ currentKbName }}
        </NButton>
        <NButton size="tiny" ghost class="skill-selector-btn" @click="showSkillModal = true">
          <template #icon><NIcon size="14"><Sparkles /></NIcon></template>
          {{ selectedSkillName }}
        </NButton>
        <NButton size="tiny" ghost class="search-trigger-btn" @click="openQuestionsModal">
          <template #icon><NIcon size="14"><List /></NIcon></template>
          {{ t('chat.historyQuestions') }}
        </NButton>
        <NButton size="tiny" ghost class="search-trigger-btn" :type="showSearch ? 'primary' : 'default'" @click="showSearch ? closeSearch() : openSearch()">
          <template #icon><NIcon size="14"><Search /></NIcon></template>
          {{ t('chat.findRecords') }}
        </NButton>
        <div class="toolbar-right">
          <button
            v-if="contextTokens > 0"
            type="button"
            class="context-meter"
            :class="[contextRatioClass, { clickable: !!conversationId }]"
            :disabled="!conversationId"
            :title="t('chat.contextTokensTip', { pct: contextRatioPct })"
            @click="openContextModal"
          >
            <span class="context-meter-text">{{ t('chat.contextTokens', { used: formatTokens(contextTokens), total: formatTokens(auth.contextWindow) }) }}</span>
            <span class="context-meter-bar"><span class="context-meter-fill" :style="{ width: contextRatioPct + '%' }"></span></span>
          </button>
        </div>
      </div>
      <div class="chat-input-area">
        <NButton
          class="attach-btn"
          :disabled="isStreaming || isReadonly || !auth.llmConfigured"
          :title="t('workspace.attachFile')"
          @click="openFileModal"
        >
          <template #icon><NIcon size="20"><Add /></NIcon></template>
        </NButton>
        <NInput
          ref="inputRef"
          v-model:value="inputText"
          type="textarea"
          :placeholder="auth.llmConfigured ? t('chat.inputPlaceholder') : t('chat.configApiKey')"
          :autosize="{ minRows: 1, maxRows: 4 }"
          :disabled="isStreaming || !auth.llmConfigured"
          @keydown="handleKeydown"
          @compositionstart="isComposing = true"
          @compositionend="isComposing = false"
        >
          <template #suffix>
            <NTooltip trigger="hover">
              <template #trigger>
                <NSwitch
                  v-model:value="sendMode"
                  checked-value="shiftEnter"
                  unchecked-value="enter"
                  size="small"
                  class="send-mode-switch"
                />
              </template>
              {{ sendMode === 'shiftEnter' ? t('chat.sendModeShiftEnterHint') : t('chat.sendModeEnterHint') }}
            </NTooltip>
          </template>
        </NInput>
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

      <AppModal
        v-model:show="showContextModal"
        :title="t('chat.contextModal.title')"
        size="wide"
      >
        <NSpin :show="ctxLoading">
          <p class="ctx-intro">{{ t('chat.contextModal.intro') }}</p>
          <div class="ctx-head">
            <div class="ctx-total" :class="contextRatioClass">
              {{ t('chat.contextTokens', { used: formatTokens(contextTokens), total: formatTokens(auth.contextWindow) }) }}
              <span class="ctx-pct">{{ contextRatioPct }}%</span>
            </div>
            <div v-if="hasBreakdown" class="ctx-breakdown">
              {{ t('chat.contextModal.breakdown', { persistent: contextPersistentPct, transient: contextTransientPct }) }}
            </div>
            <div class="ctx-cursor">
              {{ t('chat.contextModal.cursor', { done: summaryMsgCount, total: totalMessages }) }}
            </div>
          </div>

          <div class="ctx-section-title">{{ t('chat.contextModal.summaryTitle') }}</div>

          <NInput
            v-if="ctxEditing"
            v-model:value="ctxDraft"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 18 }"
            :disabled="ctxBusy"
          />
          <template v-else>
            <div v-if="ctxSummaryParagraphs.length === 0" class="ctx-empty">
              {{ t('chat.contextModal.empty') }}
            </div>
            <ol v-else class="ctx-para-list">
              <li v-for="(p, i) in ctxSummaryParagraphs" :key="i" class="ctx-para">{{ p }}</li>
            </ol>
          </template>
        </NSpin>

        <template #footer>
          <div class="ctx-footer">
            <template v-if="ctxEditing">
              <NButton :disabled="ctxBusy" @click="ctxEditing = false; ctxDraft = ctxSummaryText">
                {{ t('chat.contextModal.cancel') }}
              </NButton>
              <NButton type="primary" :loading="ctxBusy" :disabled="!ctxDirty" @click="saveSummaryEdit">
                {{ t('chat.contextModal.save') }}
              </NButton>
            </template>
            <template v-else>
              <NButton
                :disabled="ctxBusy || isStreaming || isReadonly || ctxSummaryParagraphs.length === 0"
                @click="ctxEditing = true"
              >
                <template #icon><NIcon size="14"><Create /></NIcon></template>
                {{ t('chat.contextModal.edit') }}
              </NButton>
              <NButton
                type="primary"
                :loading="ctxBusy"
                :disabled="ctxBusy || isStreaming || isReadonly || summaryMsgCount >= totalMessages"
                @click="runCompact"
              >
                {{ t('chat.contextModal.compact') }}
              </NButton>
            </template>
          </div>
        </template>
      </AppModal>

      <AppModal
        v-model:show="showWsModal"
        :title="t('workspace.selectDirTitle')"
        size="detail"
      >
        <div class="ws-crumbs">
          <NButton text size="small" :type="wsCurrentPath ? 'primary' : 'default'" @click="wsCrumb('')">
            {{ t('workspace.breadcrumbRoot') }}
          </NButton>
          <template v-for="seg in wsCrumbSegments" :key="seg.path">
            <span class="ws-sep">/</span>
            <NButton
              text
              size="small"
              :type="seg.path === wsCurrentPath ? 'default' : 'primary'"
              @click="wsCrumb(seg.path)"
            >{{ seg.name }}</NButton>
          </template>
        </div>

        <NSpin :show="wsLoading">
          <div class="ws-dir-list">
            <div v-if="wsDirs.length === 0 && !wsLoading" class="ws-empty">
              {{ t('workspace.noSubdir') }}
            </div>
            <div
              v-for="d in wsDirs"
              :key="d.rel_path"
              class="ws-dir-row"
              @click="wsEnterDir(d)"
            >
              <NIcon size="18" class="ws-folder"><Folder /></NIcon>
              <span class="ws-dir-name">{{ d.name }}</span>
              <span class="ws-enter">›</span>
            </div>
          </div>
        </NSpin>

        <template #footer>
          <div class="ws-footer">
            <NInput
              v-if="wsCreating"
              v-model:value="wsNewName"
              size="small"
              class="ws-create-input"
              :placeholder="t('workspace.subdirName')"
              @keydown.enter="wsCreateDir"
            />
            <NButton
              @click="wsCreating ? wsCreateDir() : wsToggleCreate()"
            >
              <template v-if="!wsCreating" #icon><NIcon size="14"><Create /></NIcon></template>
              {{ wsCreating ? t('workspace.create') : t('workspace.createSubdir') }}
            </NButton>
            <NButton type="primary" @click="wsConfirmDir">
              {{ t('workspace.selectHere') }}
            </NButton>
          </div>
        </template>
      </AppModal>

      <AppModal
        v-model:show="showFileModal"
        :title="t('workspace.pickFileTitle')"
        size="detail"
      >
        <div
          class="fp-dropzone"
          :class="{ 'fp-dragging': fpDragging }"
          @dragenter="fpOnDragEnter"
          @dragleave="fpOnDragLeave"
          @dragover="fpOnDragOver"
          @drop="fpOnDrop"
        >
          <p class="fp-hint">{{ t('workspace.pickFileHint') }}</p>
          <p class="fp-drop-hint">
            <NIcon size="14" class="fp-drop-icon"><CloudUploadOutline /></NIcon>
            {{ t('workspace.dropFilesHere') }}
          </p>
          <div class="ws-crumbs">
            <NButton text size="small" :type="fpPath ? 'primary' : 'default'" @click="fpCrumb('')">
              {{ t('workspace.breadcrumbRoot') }}
            </NButton>
            <template v-for="seg in fpCrumbSegments" :key="seg.path">
              <span class="ws-sep">/</span>
              <NButton
                text
                size="small"
                :type="seg.path === fpPath ? 'default' : 'primary'"
                @click="fpCrumb(seg.path)"
              >{{ seg.name }}</NButton>
            </template>
          </div>

          <NSpin :show="fpLoading">
            <div class="ws-dir-list">
              <div v-if="fpEntries.length === 0 && !fpLoading" class="ws-empty">
                {{ t('workspace.empty') }}
              </div>
              <div
                v-for="e in fpEntries"
                :key="e.rel_path"
                class="ws-dir-row"
                :class="{ 'ws-file-row': e.type === 'file' }"
                @click="e.type === 'dir' ? fpEnterDir(e) : fpSelectFile(e)"
              >
                <NIcon v-if="e.type === 'dir'" size="18" class="ws-folder"><Folder /></NIcon>
                <NIcon v-else size="18" class="ws-file-icon"><DocumentText /></NIcon>
                <span class="ws-dir-name">{{ e.name }}</span>
                <span v-if="e.type === 'file' && e.size != null" class="ws-file-size">{{ (e.size / 1024).toFixed(1) }} KB</span>
                <span v-else class="ws-enter">›</span>
              </div>
            </div>
          </NSpin>

          <!-- Drag overlay -->
          <div v-if="fpDragging" class="fp-overlay">
            <NIcon size="40" class="fp-overlay-icon"><CloudUploadOutline /></NIcon>
            <div class="fp-overlay-text">{{ t('workspace.dropHere') }}</div>
          </div>
          <!-- Uploading overlay -->
          <div v-else-if="fpUploading" class="fp-overlay">
            <NSpin size="large" />
            <div class="fp-overlay-text">{{ t('workspace.uploading') }}</div>
          </div>
        </div>

        <template #footer>
          <div class="ws-footer">
            <NButton @click="showFileModal = false">{{ t('workspace.cancel') }}</NButton>
          </div>
        </template>
      </AppModal>
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
  padding-top: 2px; /* prevent hover border-top clipping */
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
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
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
.question-row-text {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.question-row-meta {
  flex-shrink: 0;
  margin-left: 12px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
}
.kb-pick-inner { display: flex; align-items: flex-start; gap: 10px; }
.kb-pick-avatar {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  background: var(--color-primary-soft);
}
.kb-pick-avatar-none { background: var(--color-border); }
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
/* 3-column grid: equal widths, 3 per row */
.skill-pick-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
  padding-top: 2px; /* prevent hover border-top clipping from overflow:auto parent */
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
  font-weight: 400;
}
.chat-input-area {
  display: flex;
  gap: var(--space-2);
  padding: 0 0 var(--space-3);
  flex-shrink: 0;
}

/* ── Workspace directory selector (button above the input box) ── */
.ws-crumbs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: var(--space-3);
  font-size: 13px;
}
.ws-sep {
  color: var(--color-text-muted, #999);
  margin: 0 2px;
}
.ws-dir-list {
  min-height: 120px;
  max-height: 280px;
  overflow-y: auto;
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 10px;
  padding: var(--space-2);
}
.ws-empty {
  color: var(--color-text-muted, #999);
  text-align: center;
  padding: var(--space-5) 0;
}
.ws-dir-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.ws-dir-row:hover {
  background: var(--color-hover, rgba(0, 0, 0, 0.04));
}
.ws-dir-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ws-folder {
  color: var(--color-warning, #f0a020);
}
.ws-enter {
  color: var(--color-text-muted, #999);
  font-size: 18px;
  line-height: 1;
}
.ws-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-2);
}
.ws-create-input {
  width: 180px;
}

/* ── Right-aligned toolbar group: context meter + send-mode switch ── */
.toolbar-right {
  margin-left: auto;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.send-mode-switch {
  flex-shrink: 0;
}

/* ── Attach ("+") button left of the input box ── */
.attach-btn {
  flex-shrink: 0;
  align-self: flex-end;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  transition: transform 0.15s ease, background 0.15s ease, color 0.15s ease;
}
.attach-btn:hover:not(:disabled) {
  transform: translateY(-1px);
}

/* ── File picker modal ── */
.fp-hint {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--color-text-muted);
}
.fp-dropzone {
  position: relative;
  border-radius: 12px;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.fp-dropzone.fp-dragging {
  background: var(--color-primary-soft, rgba(64, 152, 252, 0.08));
  box-shadow: inset 0 0 0 2px var(--color-primary, #4098fc);
}
.fp-drop-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--color-text-muted);
}
.fp-drop-icon {
  color: var(--color-primary, #4098fc);
}
.fp-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border-radius: 12px;
  background: var(--color-overlay, rgba(255, 255, 255, 0.72));
  backdrop-filter: blur(2px);
  pointer-events: none;
  z-index: 2;
}
.fp-overlay-icon {
  color: var(--color-primary, #4098fc);
}
.fp-overlay-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}
.ws-file-icon {
  color: var(--color-primary, #4098fc);
}
.ws-file-size {
  margin-left: auto;
  font-size: 12px;
  color: var(--color-text-muted, #999);
  flex-shrink: 0;
  white-space: nowrap;
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
  /* Rendered as a <button> so the meter is keyboard-focusable; strip the UA
     button chrome so it still looks like the original inline badge. */
  font: inherit;
  appearance: none;
  -webkit-appearance: none;
  cursor: default;
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
.context-meter.clickable {
  cursor: pointer;
  transition: border-color .2s ease, background .2s ease;
}
.context-meter.clickable:hover {
  border-color: var(--color-primary);
  background: var(--color-bg-hover, var(--color-surface));
}
.context-meter:disabled { cursor: default; }

/* ── Context inspector modal ── */
.ctx-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  margin-bottom: 14px;
}
.ctx-total {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--color-text);
}
.ctx-total.warn { color: #f0a020; }
.ctx-total.danger { color: #e0413e; }
.ctx-pct { margin-left: 8px; }
.ctx-intro {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.65;
  color: var(--color-text-muted);
}
.ctx-breakdown,
.ctx-cursor {
  font-size: 12px;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
.ctx-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 8px;
}
.ctx-para-list {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 42vh;
  overflow-y: auto;
}
.ctx-para {
  font-size: 13px;
  line-height: 1.65;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.ctx-empty {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-muted);
  padding: 16px 12px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
}
.ctx-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

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

/* ── Unread answer red dot on the history button ── */
.history-btn-wrap {
  position: relative;
  display: inline-flex;
}
.history-unread-dot {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-danger, #e5484d);
  border: 2px solid var(--color-surface);
  box-sizing: content-box;
  pointer-events: none;
}
/* ── Unread answer badge on a conversation row ── */
.conv-unread-badge {
  font-size: var(--text-xs);
  color: #fff;
  background: var(--color-danger, #e5484d);
  padding: 1px 6px;
  border-radius: 999px;
  line-height: 1.4;
  white-space: nowrap;
}
</style>
