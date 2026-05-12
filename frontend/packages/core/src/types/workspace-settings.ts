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

export type MCPCredentialMode = 'org' | 'workspace' | 'user'
export type MCPCredentialSource = 'org' | 'workspace' | 'user' | 'needs_setup' | null

export interface MCPServerItem {
  server_id: string
  name: string
  server_url: string
  transport: string
  enabled: boolean
  scope: 'org' | 'workspace'
  credential_mode: MCPCredentialMode
  credential_source: MCPCredentialSource
  credential_shared_by: string | null
}

export interface WorkspaceMCP {
  org_servers: MCPServerItem[]
  workspace_servers: MCPServerItem[]
}
