import type { Conversation } from './conversation'

export interface Topic {
  id: string
  title: string
  sandbox_mode: string | null
  max_participants: number
  creator_user_id: string
  is_archived: boolean
  is_pinned: boolean
  created_at: string
  updated_at: string
  last_activity_at: string
  /** Present on list responses; absent on single-topic responses. */
  participant_count?: number
}

export interface TopicParticipant {
  id: string
  topic_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
  /** Hydrated by the backend from the User row. */
  display_name?: string | null
  email?: string | null
}

export interface TopicCreateResponse {
  topic: Topic
  conversation: { id: string; title: string; topic_id: string }
  participants: TopicParticipant[]
}

export interface TopicDetailResponse {
  topic: Topic
  participants: TopicParticipant[]
  conversations: Conversation[]
}
