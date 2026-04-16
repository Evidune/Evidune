import type {
  ChatResponse,
  ConversationMode,
  ConversationHistory,
  ConversationSummary,
  FeedbackRequest,
  FeedbackResponse,
  Skill,
  TaskEvent,
} from './types'

const BASE = ''

export async function fetchSkills(): Promise<Skill[]> {
  const resp = await fetch(`${BASE}/api/skills`)
  if (!resp.ok) return []
  return resp.json()
}

export async function sendMessage(
  text: string,
  conversationId: string,
  identity?: string,
  mode?: ConversationMode,
): Promise<ChatResponse> {
  const resp = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      conversation_id: conversationId,
      ...(identity ? { identity } : {}),
      ...(mode ? { mode } : {}),
    }),
  })
  if (!resp.ok) {
    return { text: '', conversation_id: conversationId, error: `HTTP ${resp.status}` }
  }
  return resp.json()
}

export function streamMessage(
  text: string,
  conversationId: string,
  handlers: {
    onTask: (event: TaskEvent) => void
    onDone: (response: ChatResponse) => void
    onError: (message: string) => void
  },
  identity?: string,
  mode?: ConversationMode,
): () => void {
  const params = new URLSearchParams({
    text,
    conversation_id: conversationId,
  })
  if (identity) params.set('identity', identity)
  if (mode) params.set('mode', mode)
  const source = new EventSource(`${BASE}/api/chat/stream?${params.toString()}`)
  source.addEventListener('task', event => {
    handlers.onTask(JSON.parse(event.data) as TaskEvent)
  })
  source.addEventListener('done', event => {
    source.close()
    handlers.onDone(JSON.parse(event.data) as ChatResponse)
  })
  source.addEventListener('error', () => {
    source.close()
    handlers.onError('Streaming failed')
  })
  return () => source.close()
}

export async function sendFeedback(req: FeedbackRequest): Promise<FeedbackResponse> {
  const resp = await fetch(`${BASE}/api/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) {
    return { error: `HTTP ${resp.status}` }
  }
  return resp.json()
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const resp = await fetch(`${BASE}/api/conversations`)
  if (!resp.ok) return []
  return resp.json()
}

export async function fetchConversationHistory(
  id: string,
): Promise<ConversationHistory | null> {
  const resp = await fetch(`${BASE}/api/conversations/${encodeURIComponent(id)}/history`)
  if (!resp.ok) return null
  return resp.json()
}

export async function deleteConversation(id: string): Promise<boolean> {
  const resp = await fetch(`${BASE}/api/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  return resp.ok
}

export async function archiveConversation(id: string): Promise<boolean> {
  const resp = await fetch(
    `${BASE}/api/conversations/${encodeURIComponent(id)}/archive`,
    { method: 'POST' },
  )
  return resp.ok
}
