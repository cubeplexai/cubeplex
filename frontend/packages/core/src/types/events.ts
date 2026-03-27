export type AgentEventType =
  | 'chain_start'
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, any>
}

export interface ChainStartEvent extends AgentEvent {
  type: 'chain_start'
  data: { input: string }
}

export interface TextDeltaEvent extends AgentEvent {
  type: 'text_delta'
  data: {
    content: string
    usage?: { input_tokens: number; output_tokens: number }
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
    arguments: Record<string, any>
  }
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
  data: Record<string, any>
}
