export type MCPTransport = 'streamable_http' | 'sse'
export type MCPAuthMethod = 'static' | 'oauth' | 'none'
export type MCPCredentialScope = 'org' | 'workspace' | 'user' | 'none'

export interface MCPCredentialRef {
  id: string
  name: string
  has_value: boolean
}

export interface MCPToolEntry {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface MCPServer {
  id: string
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: MCPCredentialScope
  credential: MCPCredentialRef | null
  owner_workspace_id: string | null
  headers: Record<string, string>
  tools_cache: MCPToolEntry[] | null
  authed: boolean
  last_error: string | null
  last_discovered_at: string | null
  timeout: number
  sse_read_timeout: number
  created_by_user_id: string
  created_at: string
  updated_at: string
}

export interface MCPServerCreateAdminBody {
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: 'org' | 'user' | 'none'
  credential_plaintext?: string
  credential_name?: string
  headers?: Record<string, string>
  timeout?: number
  sse_read_timeout?: number
}

export interface MCPServerCreateWSBody {
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: 'workspace' | 'user' | 'none'
  credential_plaintext?: string
  credential_name?: string
  headers?: Record<string, string>
  timeout?: number
  sse_read_timeout?: number
}

export interface MCPServerPatchBody {
  name?: string
  server_url?: string
  transport?: MCPTransport
  credential_plaintext?: string
  headers?: Record<string, string>
  timeout?: number
  sse_read_timeout?: number
}

export interface MCPTestConnectionBody {
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_scope: MCPCredentialScope
  credential_plaintext?: string
  headers?: Record<string, string>
  timeout?: number
  sse_read_timeout?: number
}

export interface MCPTestConnectionResult {
  success: boolean
  tools: MCPToolEntry[] | null
  error: string | null
}

export interface WorkspaceOverride {
  workspace_id: string
  enabled: boolean
}

export interface MCPOverrideUpdateBody {
  workspace_id: string
  enabled: boolean
}

export interface MCPServerListWS {
  owned: MCPServer[]
  inherited: MCPServer[]
}

export interface PromoteBody {
  share_credential: boolean
}

export interface CredentialUpsertBody {
  plaintext: string
  name?: string
}

export interface CredentialStatus {
  has_value: boolean
}
