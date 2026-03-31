// frontend/packages/core/src/types/message.ts
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { name: string; arguments: Record<string, unknown> }[] | null
  reasoning?: string | null
  name?: string | null  // for tool messages
  created_at?: string
}
