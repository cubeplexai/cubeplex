export interface ConversationParticipant {
  id: string
  conversation_id: string
  user_id: string
  joined_at: string
  display_name?: string | null
  email?: string | null
  avatar_url?: string | null
  avatar_seed?: string | null
}
