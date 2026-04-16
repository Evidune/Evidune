export type SignalType = 'thumbs_up' | 'thumbs_down' | 'copied' | 'regenerated'
export type ConversationMode = 'plan' | 'execute'

export interface PlanItem {
  step: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface ConversationPlan {
  explanation: string
  items: PlanItem[]
}

export interface ToolTraceEntry {
  role?: string
  name: string
  arguments: Record<string, unknown>
  result: string
  is_error: boolean
}

export interface TaskEvent {
  sequence: number
  type: string
  phase?: string
  role?: string
  message: string
  data?: Record<string, unknown>
  created_at?: string
}

export interface BudgetSummary {
  token_budget?: number
  token_used?: number
  tool_call_budget?: number
  tool_calls_used?: number
  wall_clock_budget_s?: number
  elapsed_ms?: number
  max_rounds?: number
  rounds_used?: number
  stopped_reason?: string
}

export interface ConvergenceSummary {
  decision?: string
  accepted_artifacts?: number[]
  rejected_artifacts?: number[]
  rationale?: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  skills?: string[]
  executionIds?: number[]
  feedback?: Partial<Record<SignalType, boolean>>
  toolTrace?: ToolTraceEntry[]
  taskId?: string
  squad?: string | null
  taskStatus?: string | null
  taskEvents?: TaskEvent[]
  convergenceSummary?: ConvergenceSummary | null
  budgetSummary?: BudgetSummary | null
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
  identity?: string | null
  mode?: ConversationMode | null
  plan?: ConversationPlan | null
  new_title?: string | null
  tool_trace?: ToolTraceEntry[]
  task_id?: string | null
  squad?: string | null
  task_status?: string | null
  task_events?: TaskEvent[]
  convergence_summary?: ConvergenceSummary | null
  budget_summary?: BudgetSummary | null
  error?: string
}

export interface ConversationSummary {
  id: string
  channel: string
  identity?: string
  mode: ConversationMode
  squad_profile?: string
  has_plan?: boolean
  plan?: ConversationPlan | null
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
  lifecycle_decision?: 'keep' | 'rewrite' | 'rollback' | 'disable' | null
  skill_status?: 'active' | 'pending_review' | 'disabled' | 'rolled_back'
  harness_task_id?: string
  error?: string
}
