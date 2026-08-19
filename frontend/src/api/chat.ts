// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
import type { Conversation, SSEEvent } from '@/types'
import { i18n } from '@/i18n'
import { useAuthStore } from '@/stores/auth'

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// Thin 401 handler for one-shot responses (SSE, deletes). Unlike the old
// behaviour it does NOT wipe storage or force a full-page reload — a full
// reload would clear sessionStorage (where the refresh token lives), silently
// killing transparent renewal for every subsequent request. Instead we let the
// global auth store own logout via SPA navigation.
function handleResponse(r: Response) {
  if (r.status === 401) {
    const auth = useAuthStore()
    // By the time we reach here, authFetch has already attempted one transparent
    // refresh and retried. If we still got 401, the session is genuinely dead, so
    // log out via SPA navigation (no full-page reload — that would wipe
    // sessionStorage and break other in-flight requests).
    if (!auth.refreshToken) auth.logout()
    throw new Error(i18n.global.t('errors.loginExpiredShort'))
  }
  return r
}

// fetch wrapper that mirrors the axios client's transparent refresh: on 401 we
// attempt one silent token refresh, then retry the request with the new token
// (authHeaders reads the refreshed token from localStorage). This keeps every
// chat API call resilient to the 30-min access-token expiry without a reload.
async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const doFetch = () =>
    fetch(url, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } })

  let res = await doFetch()
  if (res.status === 401) {
    const auth = useAuthStore()
    if (await auth.refresh()) {
      res = await doFetch()
    }
  }
  return handleResponse(res)
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
  const payload = JSON.stringify({
    query,
    kb_id: kbId,
    conversation_id: conversationId,
    skill_id: skillId,
    skip_cache: skipCache,
    resume_action: resumeAction ?? undefined,
    subdir: workspaceDir ?? '',
    timezone: timezone ?? undefined,
    attach: attach ?? false,
  })

  const doFetch = () =>
    fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
      },
      body: payload,
      signal,
    })

  let response = await doFetch()
  // Transparent refresh on 401, then retry the stream once with the new token.
  if (response.status === 401) {
    if (await useAuthStore().refresh()) {
      response = await doFetch()
    }
  }

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
export const listConversations = (search?: string) => {
  const qs = search ? `?search=${encodeURIComponent(search)}` : ""
  return authFetch("/api/conversations" + qs).then((r) => r.json()) as Promise<Conversation[]>
}
export const getConversation = (id: string, includeMessages = true) =>
  authFetch(`/api/conversations/${id}?include_messages=${includeMessages}`).then((r) => r.json())

// Server-side paginated conversation messages: page is 1-based (oldest first); pass 'last' to fetch the newest page
export const getConversationMessages = (id: string, page: number | string = 'last', pageSize = 10) => {
  const qs = new URLSearchParams()
  qs.set('page', String(page))
  qs.set('page_size', String(pageSize))
  return authFetch(`/api/conversations/${id}/messages?${qs.toString()}`)
    .then((r) => r.json())
}

export interface ConversationDeleteResult {
  status: string // 'deleting' — the row is gone, child rows are purged in the background
  aborted_run: boolean // whether a live generation was cancelled to make the delete possible
}

// 202 Accepted: the backend removes the conversation row synchronously (so it is
// gone from every listing immediately) and purges messages / agent steps /
// archived memory in a throttled background task. Failures throw the bare error
// code so the caller can localize it and roll its optimistic UI update back.
export const deleteConversation = async (id: string): Promise<ConversationDeleteResult> => {
  const r = handleResponse(
    await authFetch(`/api/conversations/${id}`, { method: 'DELETE' }),
  )
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body?.detail || `HTTP_${r.status}`)
  return body as ConversationDeleteResult
}

// ── Persistent context (compressed summary + folding cursor) ──
// Rename a conversation (title only). Empty title is rejected by the backend.
export const renameConversation = async (id: string, title: string): Promise<Conversation> => {
  const r = handleResponse(
    await authFetch(`/api/conversations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  )
  return (await r.json()) as Conversation
}
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
  authFetch(`/api/conversations/${id}/summary`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ summary_text: summaryText }),
  }).then(summaryStateResponse)

// Fold the oldest `fraction` of the un-summarized history into the summary.
export const compactConversation = (id: string, fraction = 0.5) =>
  authFetch(`/api/conversations/${id}/compact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fraction }),
  }).then(summaryStateResponse)

// Delete one fold segment from the summary by content match. The backend removes
// the first segment whose (stripped) text equals `segmentText` and leaves the
// folding cursor untouched.
export const deleteSummarySegment = (id: string, segmentText: string) =>
  authFetch(`/api/conversations/${id}/summary/segments`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ segment_text: segmentText }),
  }).then(summaryStateResponse)

// Set or clear a conversation's pinned instruction. Empty string clears it. The
// backend rejects values longer than PIN_INSTRUCTION_MAX_CHARS with
// PIN_INSTRUCTION_TOO_LONG. Returns the persisted value.
export const putPinInstruction = (id: string, pinnedInstruction: string) =>
  authFetch(`/api/conversations/${id}/pin`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pinned_instruction: pinnedInstruction }),
  })
    .then((r) => r.json()) as Promise<{
    pinned_instruction: string
    // Non-blocking budget warning when the pin eats too large a share of the
    // context window (it is re-sent in the system prefix on every turn).
    warnings?: { code: string; params?: Record<string, unknown> }[]
  }>

// Restore suspension state after refresh: return the conversation's pending quota suspension awaiting user confirmation (or null)
export const getPendingLimit = (id: string) =>
  authFetch(`/api/conversations/${id}/pending`)
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
  authFetch(`/api/conversations/${id}/status`)
    .then((r) => r.json())
    .catch(() => ({ running: false, pending: null }))
