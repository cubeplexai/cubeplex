// frontend/packages/core/src/types/events.ts
export type ContentBlock =
  | { type: 'reasoning'; content: string; started_at?: number; duration_ms?: number }
  | { type: 'text'; content: string }
  | { type: 'tool_call'; name: string; arguments: Record<string, unknown>; tool_call_id: string }

export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'
  | 'status'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null    // null = main agent, "task:xxx" = subagent
  agent_name: string | null  // subagent description
}

export interface TextDeltaEvent extends AgentEvent {
  type: 'text_delta'
  data: { content: string; usage?: { input_tokens: number; output_tokens: number } }
}

export interface ReasoningEvent extends AgentEvent {
  type: 'reasoning'
  data: { content: string }
}

export interface ToolCallEvent extends AgentEvent {
  type: 'tool_call'
  data: { tool_call_id: string; name: string; arguments: Record<string, unknown> }
}

export interface ToolResultEvent extends AgentEvent {
  type: 'tool_result'
  data: { tool_name: string; content: string }
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: { error_code: string; message: string; details?: string }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, unknown>
}

export type StatusPhase = 'sandbox_creating' | 'sandbox_ready'

export interface StatusEvent extends AgentEvent {
  type: 'status'
  data: { phase: StatusPhase }
}
