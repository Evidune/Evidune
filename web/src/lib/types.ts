export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  skills?: string[]
}

export interface Skill {
  name: string
  description: string
}

export interface ChatResponse {
  text: string
  conversation_id: string
  skills?: string[]
  error?: string
}
