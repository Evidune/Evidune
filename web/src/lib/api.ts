import type {
  ChatResponse,
  ConversationHistory,
  ConversationSummary,
  FeedbackRequest,
  FeedbackResponse,
  Skill,
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
  persona?: string,
): Promise<ChatResponse> {
  const resp = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, conversation_id: conversationId, ...(persona ? { persona } : {}) }),
  })
  if (!resp.ok) {
    return { text: '', conversation_id: conversationId, error: `HTTP ${resp.status}` }
  }
  return resp.json()
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
