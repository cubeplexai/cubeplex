export type SpanKind = 'agent' | 'turn' | 'chat' | 'tool' | 'other'

export interface TokenUsage {
  input: number
  output: number
  cache_read: number
  cache_write: number
}

export interface ChatMessage {
  role: string
  parts: Array<Record<string, unknown>>
}

export interface ToolDefinition {
  name: string
  description?: string | null
  parameters?: Record<string, unknown> | null
}

export interface LlmCallPayload {
  model: string
  provider?: string | null
  request_max_tokens?: number | null
  request_temperature?: number | null
  request_stream?: boolean | null
  tokens: TokenUsage
  finish_reasons: string[]
  time_to_first_chunk_seconds?: number | null
  response_id?: string | null
  system_instructions: ChatMessage[]
  messages: ChatMessage[]
  output_messages: ChatMessage[]
  tools: ToolDefinition[]
  raw_request?: string | null
  raw_response?: string | null
}

export interface ToolCallPayload {
  name: string
  description?: string | null
  arguments?: string | null
  result?: string | null
  is_error: boolean
  execution_mode?: string | null
  tool_call_id?: string | null
}

export interface TurnPayload {
  index: number
  stop_reason?: string | null
  tool_calls_count: number
}

export interface SpanNode {
  span_id: string
  parent_span_id?: string | null
  name: string
  kind: SpanKind
  start_time: string
  duration_ms: number
  status_code?: string | null
  status_message?: string | null
  llm?: LlmCallPayload | null
  tool?: ToolCallPayload | null
  turn?: TurnPayload | null
  raw_attributes: Record<string, unknown>
  children: SpanNode[]
}

export interface TraceSummary {
  trace_id: string
  root_name: string
  start_time: string
  duration_ms: number
  span_count: number
  org_id?: string | null
  workspace_id?: string | null
  user_id?: string | null
  conversation_id?: string | null
  run_id?: string | null
  model?: string | null
  has_error: boolean
}

// Backend has no cursor (Tempo /api/search lacks one); list is capped by `limit`.
export interface TraceListResponse {
  traces: TraceSummary[]
}

export interface TraceDetail {
  summary: TraceSummary
  root: SpanNode
}

export interface TraceFilterValues {
  workspace_id?: string
  user_id?: string
  conversation_id?: string
  run_id?: string
  model?: string
  start?: string
  end?: string
  limit?: number
}

// Tempo hard-caps search ranges at 168h (7 days) on this deployment, so there
// is no '1m' option - a full month can't be served by a single query.
export type TimeRangePreset = '1h' | '1d' | '7d' | 'custom'

export const DEFAULT_TIME_RANGE_PRESET: TimeRangePreset = '1h'

export const DEFAULT_LIMIT = 50

// Postgres-backed dropdown options for the filter bar (see /admin/traces
// filter-options). `model` is NOT here - it is low-cardinality and sourced
// from Tempo tag-values, where the value is its own label.
export type FilterOptionKind = 'workspace' | 'user' | 'conversation'

export interface FilterOption {
  id: string
  name: string
}
