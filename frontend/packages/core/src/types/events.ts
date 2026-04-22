// frontend/packages/core/src/types/events.ts
import type { CitationData } from './citation'
export type ContentBlock =
  | {
      type: 'reasoning'
      content: string
      started_at?: number
      duration_ms?: number
    }
  | { type: 'text'; content: string }
  | {
      type: 'tool_call'
      name: string
      arguments: Record<string, unknown>
      tool_call_id: string
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

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null
  agent_name: string | null
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

export type StatusPhase = 'sandbox_creating' | 'sandbox_ready' | 'sandbox_failed'

export interface StatusEvent extends AgentEvent {
  type: 'status'
  data: { phase: StatusPhase; detail?: string }
}
