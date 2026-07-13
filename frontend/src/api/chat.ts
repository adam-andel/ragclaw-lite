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
): AsyncGenerator<SSEEvent> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ query, kb_id: kbId, conversation_id: conversationId, skill_id: skillId, skip_cache: skipCache, resume_action: resumeAction ?? undefined }),
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

// 服务端分页获取对话消息：page 为 1-based（最旧在前），传 'last' 获取最新一页
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

// 刷新后恢复挂起态：返回该对话待用户确认的限额挂起（或 null）
export const getPendingLimit = (id: string) =>
  fetch(`/api/conversations/${id}/pending`, { headers: authHeaders() })
    .then(handleResponse)
    .then((r) => r.json())
    .catch(() => null)
