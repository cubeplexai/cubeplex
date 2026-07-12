/**
 * Skill marketplace types — shared between admin and member views.
 *
 * Mirrors backend `cubeplex/api/schemas/skill.py`. Keep field names in sync.
 */

export type SkillSource = 'preinstalled' | 'uploaded'
export type InstallState = 'uninstalled' | 'installed' | 'update_available'
export type WorkspaceBindingState = 'auto' | 'enabled' | 'disabled'

export interface SkillSummary {
  id: string
  name: string
  source: SkillSource
  description: string
  current_version: string
  keywords: string[]
  install_state: InstallState
  installed_version: string | null
  workspace_bindings_count: number
  workspace_binding_state?: WorkspaceBindingState | null
  imported_from_registry_id: string | null
  imported_from_registry_name: string | null
}

export interface SkillVersionDetail {
  id: string
  version: string
  description: string
  keywords: string[]
  storage_prefix: string
  entry_file: string
  uploaded_by_user_id: string | null
  /** ISO-8601 with UTC offset. */
  created_at: string
}

export interface SkillDetail {
  id: string
  name: string
  source: SkillSource
  description: string
  current_version: string
  keywords: string[]
  versions: SkillVersionDetail[]
  install_state: InstallState
  installed_version: string | null
  auto_bind: boolean | null
}

export interface SkillFile {
  rel_path: string
  size: number
  mime: string | null
  content_hash: string
}

export interface SkillContent {
  skill_id: string
  skill_version_id: string
  name: string
  version: string
  content: string
  files: SkillFile[]
}

export interface SkillFilters {
  source?: SkillSource
  installed?: boolean
  q?: string
  tag?: string
  externalOnly?: boolean
}
