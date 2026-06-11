export type ShareScope = 'workspace' | 'org' | 'public'

export interface ConversationShare {
  id: string
  conversation_id: string
  title: string
  creator_display_name: string
  scope: ShareScope
  is_active: boolean
  url: string
  created_at: string
}

export interface PublicShare {
  id: string
  title: string
  creator_display_name: string
  scope: ShareScope
  created_at: string
  messages: unknown[]
  artifacts: PublicShareArtifact[]
}

export interface PublicShareArtifact {
  id: string
  name: string
  artifact_type: string
  path: string
  entry_file?: string | null
  mime_type?: string | null
  description?: string | null
  version: number
  created_at: string
  updated_at: string
}
