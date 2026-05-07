export interface AgentConfig {
  system_prompt: string
}

export interface SkillInstall {
  install_id: string
  skill_id: string
  name: string
  description: string
  installed_version: string
  enabled: boolean
  scope: 'org' | 'workspace'
}

export interface WorkspaceSkills {
  org_skills: SkillInstall[]
  workspace_skills: SkillInstall[]
}

export interface MCPServerItem {
  server_id: string
  name: string
  server_url: string
  transport: string
  enabled: boolean
  scope: 'org' | 'workspace'
}

export interface WorkspaceMCP {
  org_servers: MCPServerItem[]
  workspace_servers: MCPServerItem[]
}

export interface SkillCatalogEntry {
  id: string
  name: string
  description: string
  source: 'preinstalled' | 'uploaded'
  current_version: string
  keywords: string[]
  install_state: 'installed' | 'uninstalled'
  installed_version: string | null
  workspace_bindings_count: number
}
