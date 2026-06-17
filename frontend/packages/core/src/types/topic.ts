export interface Topic {
  id: string
  title: string
  sandbox_mode: string | null
  max_participants: number
  creator_user_id: string
  is_archived: boolean
  created_at: string
  updated_at: string
  last_activity_at: string
}

export interface TopicParticipant {
  id: string
  topic_id: string
  user_id: string
  role: 'owner' | 'member'
  joined_at: string
}

export interface TopicCreateResponse {
  topic: Topic
  conversation: { id: string; title: string; topic_id: string }
  participants: TopicParticipant[]
}

export interface TopicDetailResponse {
  topic: Topic
  participants: TopicParticipant[]
  conversations: { id: string; title: string; topic_id: string }[]
}
