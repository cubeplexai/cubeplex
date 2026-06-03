// frontend/packages/core/src/types/events.ts
import type { CitationData } from './citation'
// Mirrors cubepi's content-block union (cubepi/providers/base.py): TextContent,
// ThinkingContent, ToolCall. `tool_call_streaming` is a frontend-only block used
// during live SSE to render partial tool-call args before the full call lands.
export type ContentBlock =
  | { type: 'text'; text: string }
  | {
      type: 'thinking'
      thinking: string
      started_at?: number // milliseconds since epoch (live) / cubepi seconds * 1000 (bootstrap)
      duration_ms?: number
    }
  | {
      type: 'tool_call'
      id: string
      name: string
      arguments: Record<string, unknown>
    }
  | {
      type: 'tool_call_streaming'
      name: string
      args_text: string
      tool_call_id: string | null
      index: number
    }

export interface TodoItem {
  id: string | null
  description: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface ToolCallRef {
  agent_id: string | null
  tool_call_id: string | null
  index: number | null
}

export type PanelContentType =
  | 'search'
  | 'code_execute'
  | 'web_fetch'
  | 'terminal'
  | 'write_file'
  | 'generic'
  | 'artifact'
  | 'skill'
  | 'file_read'

export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_call_delta'
  | 'tool_result'
  | 'artifact'
  | 'error'
  | 'done'
  | 'citation'
  | 'status'
  | 'usage'
  | 'injected_message'
  | 'sandbox_confirm_request'
  | 'sandbox_confirm_resolved'
  | 'ask_user_request'
  | 'ask_user_resolved'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null
  agent_name: string | null
  event_id?: string
}

export interface TextDeltaEvent extends AgentEvent {
  type: 'text_delta'
  data: {
    content: string
    usage?: {
      input_tokens: number
      output_tokens: number
    }
  }
}

export interface ReasoningEvent extends AgentEvent {
  type: 'reasoning'
  data: { content: string }
}

export interface ToolCallEvent extends AgentEvent {
  type: 'tool_call'
  data: {
    tool_call_id: string
    name: string
    arguments: Record<string, unknown>
    started_at?: string
  }
}

export interface ToolCallDeltaEvent extends AgentEvent {
  type: 'tool_call_delta'
  data: {
    tool_call_id: string | null
    name: string | null
    args_delta: string
    index: number | null
  }
}

export interface ToolResultEvent extends AgentEvent {
  type: 'tool_result'
  data: {
    tool_name: string
    tool_call_id: string
    content: string
    started_at?: string
    content_type?: string
  }
}

export interface ArtifactEventData {
  action: 'created' | 'updated'
  artifact: {
    id: string
    conversation_id: string
    name: string
    artifact_type: 'file' | 'website' | 'code' | 'document' | 'image' | 'data'
    path: string
    entry_file?: string | null
    mime_type?: string | null
    description?: string | null
    created_at: string
    updated_at: string
    version: number
  }
}

export interface ArtifactEvent extends AgentEvent {
  type: 'artifact'
  data: ArtifactEventData & Record<string, unknown>
}

export interface CitationEvent extends AgentEvent {
  type: 'citation'
  data: CitationData & Record<string, unknown>
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: {
    error_code: string
    message: string
    details?: string
  }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, unknown>
}

export interface InjectedMessageEvent extends AgentEvent {
  type: 'injected_message'
  data: { content: string; steer_id: string }
}

export interface SandboxConfirmRequestEvent extends AgentEvent {
  type: 'sandbox_confirm_request'
  data: {
    question_id: string
    tool_call_id: string
    command: string
    matched_pattern: string | null
    timeout_seconds: number | null
  }
}

export interface SandboxConfirmResolvedEvent extends AgentEvent {
  type: 'sandbox_confirm_resolved'
  data: {
    question_id: string
    // 'policy_overridden' is a synthetic decision emitted by the respond path
    // when it detects a dangling pending caused by an org sandbox policy
    // change during the pause (see backend _emit_synthetic_resolved, T12).
    decision: 'approve' | 'deny' | 'policy_overridden' | null
    cancelled: boolean
    timed_out: boolean
    reason: string | null
  }
}

export interface AskOption {
  label: string
  value: string
  description: string | null
  allow_input: boolean
}

export interface AskQuestion {
  key: string
  prompt: string
  options: AskOption[] | null
  multi_select: boolean
  required: boolean
}

export interface AskUserRequestEvent extends AgentEvent {
  type: 'ask_user_request'
  data: {
    question_id: string
    questions: AskQuestion[]
    timeout_seconds: number | null
  }
}

export interface AskUserResolvedEvent extends AgentEvent {
  type: 'ask_user_resolved'
  data: {
    question_id: string
    answers: Record<string, string | string[]> | null
    cancelled: boolean
    timed_out: boolean
    // Backend explanation for a cancelled/timed_out resolve. The respond path
    // sets reason='policy_overridden' when a dangling pending is caused by an
    // org sandbox policy change during the pause (T12).
    reason?: string | null
  }
}

/**
 * Cold-start fallback payload returned by the bootstrap endpoint when the
 * Redis stream replay has aged out but the conversation still has an
 * unresolved HITL request. Mirrors backend ``serialize_pending_hitl``
 * (see :mod:`cubebox.streams.hitl_resume`).
 */
export type PendingHitl =
  | {
      run_id: string
      question_id: string
      kind: 'ask_user'
      requested_at: string
      questions: AskQuestion[]
    }
  | {
      run_id: string
      question_id: string
      kind: 'sandbox_confirm'
      requested_at: string
      tool_call_id: string
      command: string
      matched_pattern: string
    }

export type StatusPhase = 'sandbox_creating' | 'sandbox_ready' | 'sandbox_failed'

export interface StatusEvent extends AgentEvent {
  type: 'status'
  data: { phase: StatusPhase; detail?: string }
}

export interface TurnUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface SessionUsage {
  total_input_tokens: number
  total_output_tokens: number
  total_cache_read_tokens: number
  total_cache_write_tokens: number
}

export interface UsageSummary {
  turn: TurnUsage
  session: SessionUsage
  context_window: number
  context_tokens?: number
}
