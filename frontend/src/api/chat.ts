import type { Conversation, SSEEvent } from '@/types'
import { i18n } from '@/i18n'

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function handleResponse(r: Response) {
  if (r.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/login'
    throw new Error(i18n.global.t('errors.loginExpiredShort'))
  }
  return r
}

// SSE Streaming Chat
export async function* streamChat(
  query: string,
  kbId: string,
  conversationId?: string,
  skillId?: string,
  signal?: AbortSignal,
  skipCache?: boolean,
  resumeAction?: 'continue' | 'stop' | null,
  workspaceDir?: string,
  timezone?: string,
  attach?: boolean,
): AsyncGenerator<SSEEvent> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({
      query,
      kb_id: kbId,
      conversation_id: conversationId,
      skill_id: skillId,
      skip_cache: skipCache,
      resume_action: resumeAction ?? undefined,
      workspace_dir: workspaceDir ?? '',
      timezone: timezone ?? undefined,
      attach: attach ?? false,
    }),
    signal,
  })

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Request failed' }))
    yield { type: 'error', message: err.detail || 'Request failed' }
    return
  }

  const reader = response.body?.getReader()
  if (!reader) return

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event: SSEEvent = JSON.parse(line.slice(6))
          yield event
        } catch { /* skip */ }
      }
    }
  }
}

// Conversations
export const listConversations = () =>
  fetch('/api/conversations', { headers: authHeaders() }).then(handleResponse).then((r) => r.json()) as Promise<Conversation[]>

export const getConversation = (id: string, includeMessages = true) =>
  fetch(`/api/conversations/${id}?include_messages=${includeMessages}`, { headers: authHeaders() }).then(handleResponse).then((r) => r.json())

// Server-side paginated conversation messages: page is 1-based (oldest first); pass 'last' to fetch the newest page
export const getConversationMessages = (id: string, page: number | string = 'last', pageSize = 10) => {
  const qs = new URLSearchParams()
  qs.set('page', String(page))
  qs.set('page_size', String(pageSize))
  return fetch(`/api/conversations/${id}/messages?${qs.toString()}`, { headers: authHeaders() })
    .then(handleResponse)
    .then((r) => r.json())
}

export const deleteConversation = (id: string) =>
  fetch(`/api/conversations/${id}`, { method: 'DELETE', headers: authHeaders() }).then(handleResponse)

// ── Persistent context (compressed summary + folding cursor) ──
export interface ConversationSummaryState {
  conversation_id: string
  summary_text: string
  summary_msg_seq: number
  total_messages: number
  summary_archived_count: number // how many L0 folds have been archived to vector/BM25 memory
  min_compact_tok: number // min un-summarized history mass (tokens) before manual compaction may start
}

// The backend throws bare error codes (SUMMARY_LLM_FAILED / NOTHING_TO_COMPACT /
// CONVERSATION_BUSY); pass them through untouched so backendErrorMessage() can
// localize them at the call site.
async function summaryStateResponse(r: Response): Promise<ConversationSummaryState> {
  handleResponse(r)
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.detail || `HTTP_${r.status}`)
  return body as ConversationSummaryState
}

// Replace the summary text. The folding cursor is server-side immutable here.
export const updateConversationSummary = (id: string, summaryText: string) =>
  fetch(`/api/conversations/${id}/summary`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ summary_text: summaryText }),
  }).then(summaryStateResponse)

// Fold the oldest `fraction` of the un-summarized history into the summary.
export const compactConversation = (id: string, fraction = 0.5) =>
  fetch(`/api/conversations/${id}/compact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ fraction }),
  }).then(summaryStateResponse)

// Delete one fold segment from the summary by content match. The backend removes
// the first segment whose (stripped) text equals `segmentText` and leaves the
// folding cursor untouched.
export const deleteSummarySegment = (id: string, segmentText: string) =>
  fetch(`/api/conversations/${id}/summary/segments`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ segment_text: segmentText }),
  }).then(summaryStateResponse)

// Set or clear a conversation's pinned instruction. Empty string clears it. The
// backend rejects values longer than PIN_INSTRUCTION_MAX_CHARS with
// PIN_INSTRUCTION_TOO_LONG. Returns the persisted value.
export const putPinInstruction = (id: string, pinnedInstruction: string) =>
  fetch(`/api/conversations/${id}/pin`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ pinned_instruction: pinnedInstruction }),
  })
    .then(handleResponse)
    .then((r) => r.json()) as Promise<{
    pinned_instruction: string
    // Non-blocking budget warning when the pin eats too large a share of the
    // context window (it is re-sent in the system prefix on every turn).
    warnings?: { code: string; params?: Record<string, unknown> }[]
  }>

// Restore suspension state after refresh: return the conversation's pending quota suspension awaiting user confirmation (or null)
export const getPendingLimit = (id: string) =>
  fetch(`/api/conversations/${id}/pending`, { headers: authHeaders() })
    .then(handleResponse)
    .then((r) => r.json())
    .catch(() => null)

export interface ConversationRunStatus {
  running: boolean
  pending: {
    message: string
    conversation_id: string
    kind: string
    message_id: string
  } | null
}

// After a page refresh, ask the backend whether a generation is still in flight
// (running) or the conversation is durably paused (pending). The frontend uses
// this to re-attach to the live stream instead of showing a stale pause bubble.
export const getConversationStatus = (id: string) =>
  fetch(`/api/conversations/${id}/status`, { headers: authHeaders() })
    .then(handleResponse)
    .then((r) => r.json())
    .catch(() => ({ running: false, pending: null }))
