export type SignalType = 'thumbs_up' | 'thumbs_down' | 'copied' | 'regenerated'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  skills?: string[]
  executionIds?: number[]
  feedback?: Partial<Record<SignalType, boolean>>
}

export interface Skill {
  name: string
  description: string
}

export interface ChatResponse {
  text: string
  conversation_id: string
  skills?: string[]
  execution_ids?: number[]
  emerged_skill?: string | null
  facts_extracted?: number
  persona?: string | null
  new_title?: string | null
  error?: string
}

export interface ConversationSummary {
  id: string
  channel: string
  title: string
  status: 'active' | 'archived'
  created_at: string
  updated_at: string
  message_count: number
  preview: string
}

export interface ConversationHistory {
  conversation: ConversationSummary
  messages: { role: 'user' | 'assistant' | 'system'; content: string }[]
}

export interface FeedbackRequest {
  execution_id: number
  signal: SignalType
  value: boolean | number
}

export interface FeedbackResponse {
  ok?: boolean
  execution_id?: number
  signals?: Record<string, unknown>
  error?: string
}
