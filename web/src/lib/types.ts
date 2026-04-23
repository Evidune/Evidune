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
  environmentId?: string | null
  environmentStatus?: string | null
  validationSummary?: Record<string, unknown> | null
  deliverySummary?: Record<string, unknown> | null
  artifactManifest?: Record<string, unknown> | null
  skillCreation?: SkillCreation | null
  skillEvaluations?: SkillEvaluation[]
}

export interface Skill {
  name: string
  description: string
  source?: 'base' | 'emerged' | string
  status?: 'active' | 'pending_review' | 'disabled' | 'rolled_back' | string
  version?: string
  path?: string
  scripts?: string[]
  references?: string[]
  triggers?: string[]
  tags?: string[]
  created_at?: string
  updated_at?: string
  last_loaded_at?: string
  load_error?: string
  evaluation_contract?: SkillEvaluationContractSummary | null
}

export interface SkillEvaluationContractSummary {
  version: number
  criteria: string[]
  observable_metrics: string[]
  failure_modes: string[]
  min_pass_score: number
  rewrite_below_score: number
  disable_below_score: number
  min_samples_for_rewrite: number
  min_samples_for_disable: number
}

export interface SkillCreation {
  status: 'created' | 'updated' | 'reused' | 'queued' | 'failed'
  skill_name: string
  path?: string
  files?: string[]
  confidence?: number | null
  reason?: string
  duplicate_of?: string
  trigger_reason?: 'explicit_skill_request' | 'cadence' | string
}

export interface SkillEvaluation {
  skill_name: string
  execution_id: number
  contract_status?: string
  aggregate_score?: number
  criteria_scores?: Record<string, number>
  observed_metrics?: Record<string, unknown>
  missing_observations?: string[]
  reasoning?: string
}

export interface ChatResponse {
  text: string
  conversation_id: string
  skills?: string[]
  execution_ids?: number[]
  emerged_skill?: string | null
  skill_creation?: SkillCreation | null
  skill_evaluations?: SkillEvaluation[]
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
  environment_id?: string | null
  environment_status?: string | null
  validation_summary?: Record<string, unknown> | null
  delivery_summary?: Record<string, unknown> | null
  artifact_manifest?: Record<string, unknown> | null
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
