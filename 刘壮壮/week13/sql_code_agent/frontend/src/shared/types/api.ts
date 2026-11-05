export type WorkflowStage =
  | 'contextualize'
  | 'parse_database'
  | 'select_tables'
  | 'generate_sql'
  | 'validate_sql'
  | 'rewrite_sql'
  | 'execute_sql'
  | 'synthesize'

export interface ProgressUpdate {
  stage: WorkflowStage
  status: 'running' | 'completed'
  message: string
  description?: string | null
  details?: ProgressDetail[]
  duration_ms?: number | null
  tone?: 'info' | 'success' | 'warning' | 'danger'
}

export interface ProgressDetail {
  label: string
  value: string
  format: 'text' | 'status' | 'code'
}

export interface QueryResult {
  columns: string[]
  rows: Array<Array<unknown | null>>
  row_count: number
  truncated: boolean
  elapsed_ms: number
}

export interface AgentRunResult {
  request_id: string
  conversation_id: string
  answer: string
  selected_tables: string[]
  sql: string | null
  query_result: QueryResult | null
  refused: boolean
}

export interface ApiError {
  request_id: string
  code: string
  message: string
}

export interface ConversationTurnContext {
  question: string
  answer: string
  sql: string | null
}

export interface ChatRequest {
  question: string
  conversation_id: string | null
  history: ConversationTurnContext[]
}

export type ChatStreamEvent =
  | {
      type: 'meta'
      data: { request_id: string; conversation_id: string }
    }
  | { type: 'progress'; data: ProgressUpdate }
  | { type: 'answer_delta'; data: { delta: string } }
  | { type: 'result'; data: AgentRunResult }
  | { type: 'error'; data: ApiError }
