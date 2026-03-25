import type { AgentEvent } from './events'

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string | null
  events: AgentEvent[] | null
  created_at: string
}
