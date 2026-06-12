import type { Conversation, SSEEvent } from '@/types'

export function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function handleResponse(r: Response) {
  if (r.status === 401) {
    localStorage.removeItem('token')
    window.location.href = '/login'
    throw new Error('登录已过期')
  }
  return r
}

// SSE Streaming Chat
export async function* streamChat(
  query: string,
  kbId: string,
  conversationId?: string,
): AsyncGenerator<SSEEvent> {
  const response = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({ query, kb_id: kbId, conversation_id: conversationId }),
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

export const getConversation = (id: string) =>
  fetch(`/api/conversations/${id}`, { headers: authHeaders() }).then(handleResponse).then((r) => r.json())

export const deleteConversation = (id: string) =>
  fetch(`/api/conversations/${id}`, { method: 'DELETE', headers: authHeaders() }).then(handleResponse)
