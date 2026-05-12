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

// ---------------- Catalog connector types ---------------- //

export type MCPCatalogStatus = 'active' | 'deprecated' | 'disabled'

export interface MCPCatalogStaticFormField {
  name: string
  label: string
  secret: boolean
  placeholder?: string
  helper_url?: string
}

export interface MCPCatalogConnector {
  id: string
  slug: string
  name: string
  provider: string
  description: string
  server_url: string
  transport: MCPTransport
  supported_auth_methods: MCPAuthMethod[]
  default_credential_scope: MCPCredentialScope
  oauth_dcr_supported: boolean | null
  oauth_default_scope: string | null
  static_form_fields: MCPCatalogStaticFormField[] | null
  metadata: Record<string, unknown>
  status: MCPCatalogStatus
  // per-(workspace, user) install status
  org_install_id: string | null
  workspace_visible: boolean
  user_install_id: string | null
}

export interface MCPCatalogListResponse {
  items: MCPCatalogConnector[]
}

export interface MCPCatalogInstallRequest {
  auth_method: MCPAuthMethod
  auto_enable_workspaces?: boolean
  credential_plaintext?: string
  credential_name?: string
}

export interface MCPCatalogInstallWsRequest {
  auth_method: MCPAuthMethod
  credential_plaintext?: string
  credential_name?: string
}

export interface MCPInstallSwitchAuthRequest {
  auth_method: MCPAuthMethod
  credential_plaintext?: string
  credential_name?: string
}

export interface MCPCatalogInstallResult {
  install_id: string
  requires_oauth: boolean
  authed: boolean
}

export interface MCPOrgInstallOverrideRequest {
  enabled: boolean
}

export interface MCPOAuthStartResult {
  authorize_url: string
  state: string
}

// ---------------- Admin unified connector types ---------------- //

export type MCPConnectorFilter = 'all' | 'installed' | 'available' | 'custom'

export interface MCPAdminConnector {
  kind: 'catalog' | 'custom'
  id: string
  name: string
  provider: string
  description: string
  server_url: string
  transport: MCPTransport
  // Catalog-specific
  catalog_id?: string
  supported_auth_methods?: MCPAuthMethod[]
  static_form_fields?: MCPCatalogStaticFormField[] | null
  // Install state
  installed: boolean
  server?: MCPServer
  // Status display
  authed: boolean
  tool_count: number
  workspace_count: number
  last_error: string | null
}
