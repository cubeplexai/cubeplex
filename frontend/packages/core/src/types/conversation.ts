import type { ReasoningControl } from './events'

export interface Conversation {
  id: string
  title: string
  is_pinned: boolean
  topic_id?: string | null
  is_group_chat: boolean
  created_at: string
  updated_at: string
  /**
   * The user's last-sent model selection for this conversation (a tier name
   * or custom label). ``null`` means no explicit choice — use the workspace
   * default. Server-stored so the composer can restore it cross-device.
   */
  model_key: string | null
  /** The user's last-sent reasoning control for this conversation. */
  reasoning: ReasoningControl
}
