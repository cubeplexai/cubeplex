export type AgentEventType = 'chain_start' | 'llm_start' | 'llm_end' | 'tool_start' | 'tool_end' | 'chain_end' | 'error' | 'done'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, any>
}

export interface ChainStartEvent extends AgentEvent {
  type: 'chain_start'
  data: { input: string }
}

export interface LlmStartEvent extends AgentEvent {
  type: 'llm_start'
  data: Record<string, any>
}

export interface LlmEndEvent extends AgentEvent {
  type: 'llm_end'
  data: {
    output: string
    usage?: { input_tokens: number; output_tokens: number }
  }
}

export interface ToolStartEvent extends AgentEvent {
  type: 'tool_start'
  data: { tool_name: string; input: Record<string, any> }
}

export interface ToolEndEvent extends AgentEvent {
  type: 'tool_end'
  data: { tool_name: string; output: string }
}

export interface ChainEndEvent extends AgentEvent {
  type: 'chain_end'
  data: Record<string, any>
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: { error_code: string; message: string; details?: string }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, any>
}
