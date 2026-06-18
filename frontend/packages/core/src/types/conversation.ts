export interface Conversation {
  id: string
  title: string
  is_pinned: boolean
  topic_id?: string | null
  is_group_chat: boolean
  created_at: string
  updated_at: string
}
