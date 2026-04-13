import type { ChatResponse, Skill } from './types'

const BASE = ''

export async function fetchSkills(): Promise<Skill[]> {
  const resp = await fetch(`${BASE}/api/skills`)
  if (!resp.ok) return []
  return resp.json()
}

export async function sendMessage(
  text: string,
  conversationId: string,
): Promise<ChatResponse> {
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
