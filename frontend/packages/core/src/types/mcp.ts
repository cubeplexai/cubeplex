// Four-layer MCP types (templates / installs / state / effective + grants).
//
// The endpoints live under `/api/v1/ws/{ws}/mcp/templates`, `/installs`,
// `/connectors`, `/connectors/{installId}/state`, and `/admin/mcp/...`.

export type MCPTransport = 'streamable_http' | 'sse'
export type MCPAuthMethod = 'static' | 'oauth' | 'none'
export type MCPCredentialScope = 'org' | 'workspace' | 'user' | 'none'

export interface MCPToolEntry {
  name: string
  description: string | null
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
}

export interface MCPOAuthStartResult {
  authorize_url: string
  state: string
  expires_at: string
}

// ---------------- Tool-citations ---------------- //

export interface CitationConfigJSON {
  content_type: 'json' | 'text'
  source_type: string
  content_field: string | null
  mapping: Record<string, string>
  args_mapping?: Record<string, string> | null
  discriminator_field?: string | null
  discriminator_values?: string[] | null
}

// ---------------- Four-layer connector model ---------------- //

export interface MCPConnectorTemplate {
  template_id: string
  slug: string
  name: string
  provider: string
  description: string
  server_url: string
  transport: MCPTransport
  supported_auth_methods: MCPAuthMethod[]
  default_credential_policy: MCPCredentialScope
  static_form_schema: unknown[] | null
  status: 'active' | 'deprecated' | 'disabled'
  install_summary?: Record<string, unknown> | null
}

export interface MCPConnectorInstall {
  install_id: string
  connector_id: string
  template_id: string | null
  install_scope: 'org' | 'workspace'
  workspace_id: string | null
  name: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  default_credential_policy: MCPCredentialScope
  auth_status: string
  discovery_status: string
  install_state: 'active' | 'uninstalled'
  tool_count: number
  tools: MCPToolEntry[]
  tool_citations: Record<string, CitationConfigJSON>
  last_error: string | null
  auto_enroll_new_workspaces: boolean
}

export interface MCPWorkspaceConnectorState {
  workspace_id: string
  install_id: string
  connector_id: string
  enabled: boolean
  credential_policy: MCPCredentialScope
  enablement_source?: string
}

export interface MCPEffectiveConnector {
  template: MCPConnectorTemplate | null
  install: MCPConnectorInstall
  workspace_state: MCPWorkspaceConnectorState | null
  credential_policy: MCPCredentialScope
  required_grant_scope?: string | null
  credential_availability: 'available' | 'missing' | 'not_required'
  credential_source: 'org' | 'workspace' | 'user' | null
  usable: boolean
  reason: string
}

export type MCPConnectorFilter = 'all' | 'installed' | 'available' | 'custom'

export interface MCPCredentialGrantStatus {
  install_id: string
  connector_id: string
  grant_scope: 'org' | 'workspace' | 'user'
  workspace_id: string | null
  user_id: string | null
  grant_status: string
  has_value: boolean
  expires_at: string | null
}
