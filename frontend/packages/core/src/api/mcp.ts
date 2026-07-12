// MCP API helpers — template-centric semantics (post-T9/T10 backend).
//
// Admin catalog:  /api/v1/admin/mcp/catalog  (AdminCatalogRow[])
// Ws catalog:     /api/v1/ws/{ws}/mcp/catalog  (WorkspaceCatalogRow[])
// Connector paths (still keyed by connector_id): grants, invoke, discovery,
//   test-connection, tool-citations, PATCH install.

import type {
  MCPAuthMethod,
  MCPConnector,
  MCPCredentialGrantStatus,
  MCPEffectiveConnector,
  MCPOAuthStartResult,
  MCPTemplate,
  MCPTransport,
  AdminCatalogRow,
  WorkspaceCatalogRow,
} from '../types/mcp'
import { toApiError, type ApiClient } from './client'

// ---------------- Admin catalog ---------------- //

export async function adminListCatalog(client: ApiClient): Promise<{ items: AdminCatalogRow[] }> {
  const res = await client.get('/api/v1/admin/mcp/catalog')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: AdminCatalogRow[] }
}

// ---------------- Admin template CRUD ---------------- //

export interface CreateTemplateBody {
  name: string
  slug?: string
  provider?: string
  description?: string
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  default_credential_policy?: string
  supported_auth_methods?: MCPAuthMethod[]
}

export async function adminCreateTemplate(
  client: ApiClient,
  body: CreateTemplateBody,
): Promise<MCPTemplate> {
  const res = await client.post('/api/v1/admin/mcp/templates', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPTemplate
}

export async function adminDeleteTemplate(client: ApiClient, templateId: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/mcp/templates/${templateId}`)
  if (!res.ok) throw await toApiError(res)
}

// PUT …/disable to disable; DELETE …/disable to re-enable.
export async function adminSetTemplateDisabled(
  client: ApiClient,
  templateId: string,
  disabled: boolean,
): Promise<void> {
  const res = disabled
    ? await client.put(`/api/v1/admin/mcp/templates/${templateId}/disable`, {})
    : await client.del(`/api/v1/admin/mcp/templates/${templateId}/disable`)
  if (!res.ok) throw await toApiError(res)
}

export async function adminDistribute(
  client: ApiClient,
  templateId: string,
  body: { enable_existing: boolean; auto_enroll: boolean },
): Promise<AdminCatalogRow> {
  const res = await client.post(`/api/v1/admin/mcp/templates/${templateId}/distribute`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AdminCatalogRow
}

export async function adminPurgeTemplate(client: ApiClient, templateId: string): Promise<void> {
  const res = await client.post(`/api/v1/admin/mcp/templates/${templateId}/purge`, {})
  if (!res.ok) throw await toApiError(res)
}

// ---------------- Workspace catalog ---------------- //

export async function wsListCatalog(
  client: ApiClient,
  wsId: string,
): Promise<{ items: WorkspaceCatalogRow[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/catalog`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: WorkspaceCatalogRow[] }
}

export async function wsSetTemplateState(
  client: ApiClient,
  wsId: string,
  templateId: string,
  body: { enabled: boolean; credential_policy?: string },
): Promise<WorkspaceCatalogRow> {
  const res = await client.put(`/api/v1/ws/${wsId}/mcp/templates/${templateId}/state`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WorkspaceCatalogRow
}

export async function wsCreateTemplate(
  client: ApiClient,
  wsId: string,
  body: CreateTemplateBody,
): Promise<MCPTemplate> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/templates`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPTemplate
}

export async function wsPromoteTemplate(
  client: ApiClient,
  wsId: string,
  templateId: string,
): Promise<MCPTemplate> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/templates/${templateId}/promote`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPTemplate
}

// ---------------- Install-level operations (still connector_id-keyed) ---------------- //

export async function adminGetInstall(
  client: ApiClient,
  connectorId: string,
): Promise<MCPConnector> {
  const res = await client.get(`/api/v1/admin/mcp/installs/${connectorId}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

export async function adminPatchInstall(
  client: ApiClient,
  connectorId: string,
  body: unknown,
): Promise<MCPConnector> {
  const res = await client.patch(`/api/v1/admin/mcp/installs/${connectorId}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

export async function adminDeleteInstall(client: ApiClient, connectorId: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/mcp/installs/${connectorId}`)
  if (!res.ok) throw await toApiError(res)
}

// ---------------- Workspace connector state (effective — chat UI) ---------------- //

export async function wsListEffectiveConnectors(
  client: ApiClient,
  wsId: string,
): Promise<{ items: MCPEffectiveConnector[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/connectors`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPEffectiveConnector[] }
}

// ---------------- Credential grants ---------------- //

export interface CreateGrantBody {
  credential_plaintext?: string
  oauth_callback_state?: string
  name?: string
}

// Admin / org-scope grants.

export async function adminCreateOrgGrant(
  client: ApiClient,
  connectorId: string,
  body: CreateGrantBody,
): Promise<MCPCredentialGrantStatus> {
  const res = await client.post(`/api/v1/admin/mcp/installs/${connectorId}/grants/org`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCredentialGrantStatus
}

export async function adminDeleteOrgGrant(client: ApiClient, connectorId: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/mcp/installs/${connectorId}/grants/org`)
  if (!res.ok) throw await toApiError(res)
}

function oauthStartBody(): { frontend_origin?: string } {
  if (typeof window !== 'undefined') return { frontend_origin: window.location.origin }
  return {}
}

export async function adminOrgGrantOAuthStart(
  client: ApiClient,
  connectorId: string,
): Promise<MCPOAuthStartResult> {
  const res = await client.post(
    `/api/v1/admin/mcp/installs/${connectorId}/grants/org/oauth/start`,
    oauthStartBody(),
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPOAuthStartResult
}

// Workspace-scope grants.

export async function wsCreateWorkspaceGrant(
  client: ApiClient,
  wsId: string,
  connectorId: string,
  body: CreateGrantBody,
): Promise<MCPCredentialGrantStatus> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/workspace`,
    body,
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCredentialGrantStatus
}

export async function wsDeleteWorkspaceGrant(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/workspace`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsWorkspaceGrantOAuthStart(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<MCPOAuthStartResult> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/workspace/oauth/start`,
    oauthStartBody(),
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPOAuthStartResult
}

// User-scope (me) grants.

export async function wsCreateMyGrant(
  client: ApiClient,
  wsId: string,
  connectorId: string,
  body: CreateGrantBody,
): Promise<MCPCredentialGrantStatus> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/me`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCredentialGrantStatus
}

export async function wsDeleteMyGrant(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/me`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsMyGrantOAuthStart(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<MCPOAuthStartResult> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${connectorId}/grants/me/oauth/start`,
    oauthStartBody(),
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPOAuthStartResult
}

// ---------------- Discovery refresh ---------------- //

export async function adminRefreshDiscovery(
  client: ApiClient,
  connectorId: string,
  workspaceId?: string | null,
): Promise<MCPConnector> {
  const res = await client.post(`/api/v1/admin/mcp/installs/${connectorId}/refresh-discovery`, {
    workspace_id: workspaceId ?? null,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

export async function wsRefreshDiscovery(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<MCPConnector> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${connectorId}/refresh-discovery`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

// ---------------- Try It (admin + ws) ---------------- //

export interface ToolInvokeResult {
  ok: boolean
  result?: unknown
  error?: string | null
  duration_ms: number
}

export async function adminInvokeTool(
  client: ApiClient,
  connectorId: string,
  toolName: string,
  args: Record<string, unknown>,
  workspaceId?: string | null,
): Promise<ToolInvokeResult> {
  const res = await client.post(
    `/api/v1/admin/mcp/installs/${connectorId}/tools/${encodeURIComponent(toolName)}/invoke`,
    { arguments: args, workspace_id: workspaceId ?? null },
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ToolInvokeResult
}

export async function wsInvokeTool(
  client: ApiClient,
  wsId: string,
  connectorId: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<ToolInvokeResult> {
  const res = await client.post(
    `/api/v1/ws/${wsId}/mcp/installs/${connectorId}/tools/${encodeURIComponent(toolName)}/invoke`,
    { arguments: args },
  )
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ToolInvokeResult
}

// ---------------- Test connection (admin) ---------------- //

export interface TestConnectionBody {
  server_url: string
  transport: MCPTransport
  auth_method: MCPAuthMethod
  credential_plaintext?: string | null
  headers?: Record<string, string> | null
}

export interface TestConnectionResult {
  ok: boolean
  tool_count: number
  error_code: string | null
  error_message: string | null
}

export async function adminTestConnection(
  client: ApiClient,
  body: TestConnectionBody,
): Promise<TestConnectionResult> {
  const res = await client.post('/api/v1/admin/mcp/test-connection', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as TestConnectionResult
}

// ---------------- Tool citation upsert (admin) ---------------- //

export async function adminUpsertToolCitation(
  client: ApiClient,
  connectorId: string,
  toolName: string,
  config: Record<string, unknown> | null,
): Promise<MCPConnector> {
  const res = await client.put(`/api/v1/admin/mcp/installs/${connectorId}/tool-citations`, {
    tool_name: toolName,
    config,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

// ---------------- Active-tools registry (chat UI tool icons) ---------------- //

/** One MCP ``Icon`` (spec rev 2025-11-25). ``src`` is HTTP(S) URL or ``data:`` URI. */
export interface MCPToolIcon {
  src: string
  mime_type: string | null
  sizes: string[] | null
  /** ``"light"`` / ``"dark"`` when the server supplies separate variants. */
  theme: string | null
  /** data: URI when discovery materialised a remote https icon. */
  cached_src?: string | null
}

/** One MCP tool surfaced to the chat UI from a workspace's enabled installs. */
export interface MCPActiveTool {
  /** What the LLM sees and ``tool_call.name`` carries — used as the lookup key. */
  namespaced_name: string
  /** Original (bare) tool name from the MCP server's tools/list. */
  bare_name: string
  connector_id: string
  /** Install display name (the slug source for namespacing). */
  server_name: string
  server_icons: MCPToolIcon[]
  tool_icons: MCPToolIcon[]
}

export async function wsListActiveTools(
  client: ApiClient,
  wsId: string,
): Promise<{ items: MCPActiveTool[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/active-tools`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPActiveTool[] }
}
