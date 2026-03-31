// frontend/packages/core/src/types/message.ts
import type { ContentBlock } from './events'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { name: string; arguments: Record<string, unknown> }[] | null
  reasoning?: string | null
  blocks?: ContentBlock[] | null  // ordered content blocks preserving temporal order
  name?: string | null  // for tool messages
  created_at?: string
}
