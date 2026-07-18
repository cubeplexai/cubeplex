export type MemoryScope = 'personal' | 'workspace' | 'org'

export type MemoryType =
  'preference' | 'project_fact' | 'procedure' | 'correction' | 'decision' | 'org_policy'

export type MemoryStatus = 'active' | 'archived'

export interface MemoryItem {
  id: string
  scope: MemoryScope
  org_id: string | null
  workspace_id: string | null
  owner_user_id: string | null
  type: MemoryType
  content: string
  confidence: number
  status: MemoryStatus
  source_type: string
  source_conversation_id: string | null
  source_run_id: string | null
  source_artifact_id: string | null
  source_excerpt: string | null
  created_by_user_id: string
  updated_by_user_id: string | null
  created_at: string
  updated_at: string
  last_used_at: string | null
}
