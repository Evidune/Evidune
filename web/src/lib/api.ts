import type { ChatResponse, FeedbackRequest, FeedbackResponse, Skill } from './types'

const BASE = ''

export async function fetchSkills(): Promise<Skill[]> {
  const resp = await fetch(`${BASE}/api/skills`)
  if (!resp.ok) return []
  return resp.json()
}

export async function sendMessage(text: string, conversationId: string): Promise<ChatResponse> {
  const resp = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, conversation_id: conversationId }),
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
