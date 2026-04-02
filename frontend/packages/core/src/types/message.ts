// frontend/packages/core/src/types/message.ts
import type { ContentBlock } from './events'

export interface SubagentSummary {
  text: string
  tool_calls: { name: string; arguments: Record<string, unknown> }[]
  reasoning: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: {
    name: string; arguments: Record<string, unknown>; tool_call_id?: string
  }[] | null
  reasoning?: string | null
  reasoning_duration_ms?: number | null  // from backend: estimated reasoning duration
  blocks?: ContentBlock[] | null  // ordered content blocks preserving temporal order
  name?: string | null  // for tool messages
  tool_call_id?: string | null  // for tool messages: which tool_call this responds to
  subagent_events?: SubagentSummary | null  // consolidated subagent data for tool messages
  created_at?: string
}
