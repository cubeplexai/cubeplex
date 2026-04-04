// frontend/packages/core/src/types/artifact.ts
export interface Artifact {
  id: string
  conversation_id: string
  name: string
  artifact_type: 'file' | 'website' | 'code' | 'document' | 'image' | 'data'
  path: string
  entry_file?: string | null
  mime_type?: string | null
  description?: string | null
  created_at: string
  updated_at: string
  version: number
}
