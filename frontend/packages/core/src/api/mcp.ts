// Four-layer MCP API helpers (templates / installs / state / connectors / grants).

import type {
  MCPAuthMethod,
  MCPConnector,
  MCPConnectorTemplate,
  MCPCredentialGrantStatus,
  MCPEffectiveConnector,
  MCPOAuthStartResult,
  MCPTransport,
  MCPWorkspaceConnectorState,
} from '../types/mcp'
import type { AdminOrgConnector } from '../types/mcp_admin_connector'
import type { WsAvailable } from '../types/mcp_ws_available'
import { toApiError, type ApiClient } from './client'

// ---------------- Templates (public + admin) ---------------- //

export async function listTemplates(client: ApiClient): Promise<{ items: MCPConnectorTemplate[] }> {
  const res = await client.get('/api/v1/mcp/templates')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPConnectorTemplate[] }
}

export async function wsListTemplates(
  client: ApiClient,
  wsId: string,
): Promise<{ items: MCPConnectorTemplate[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/templates`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPConnectorTemplate[] }
}

export async function adminListTemplates(
  client: ApiClient,
): Promise<{ items: MCPConnectorTemplate[] }> {
  const res = await client.get('/api/v1/admin/mcp/templates')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPConnectorTemplate[] }
}

// ---------------- Installs (admin org-scope + workspace-scope) ---------------- //

export async function adminCreateInstall(client: ApiClient, body: unknown): Promise<MCPConnector> {
  const res = await client.post('/api/v1/admin/mcp/installs', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

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

export async function wsCreateInstall(
  client: ApiClient,
  wsId: string,
  body: unknown,
): Promise<MCPConnector> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
}

export async function wsDeleteInstall(
  client: ApiClient,
  wsId: string,
  connectorId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${connectorId}`)
  if (!res.ok) throw await toApiError(res)
}

// ---------------- Workspace connector state ---------------- //

export async function wsListEffectiveConnectors(
  client: ApiClient,
  wsId: string,
): Promise<{ items: MCPEffectiveConnector[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/connectors`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: MCPEffectiveConnector[] }
}

export async function wsPatchConnectorState(
  client: ApiClient,
  wsId: string,
  connectorId: string,
  body: Partial<MCPWorkspaceConnectorState>,
): Promise<MCPWorkspaceConnectorState> {
  const res = await client.patch(`/api/v1/ws/${wsId}/mcp/connectors/${connectorId}/state`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPWorkspaceConnectorState
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

// ---------------- Admin install effective lookup ---------------- //

export interface MCPAdminInstallEffective {
  connector_id: string
  usable: boolean
  reason: 'usable' | 'pending_oauth' | 'missing_org_grant' | 'grant_expired' | 'discovery_failed'
}

export async function adminGetInstallEffective(
  client: ApiClient,
  connectorId: string,
): Promise<MCPAdminInstallEffective> {
  const res = await client.get(`/api/v1/admin/mcp/installs/${connectorId}/effective`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPAdminInstallEffective
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

// ---------------- Promote ws -> org (admin) ---------------- //

export interface PromoteDistribution {
  mode: 'all' | 'selected' | 'none'
  workspace_ids?: string[] | null
}

export async function adminPromoteToOrg(
  client: ApiClient,
  connectorId: string,
  distribution: PromoteDistribution,
): Promise<MCPConnector> {
  const res = await client.post(`/api/v1/admin/mcp/installs/${connectorId}/promote-to-org`, {
    distribution,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPConnector
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

// ---------------- Admin org connectors + workspace available ---------------- //

export async function adminListConnectors(
  client: ApiClient,
): Promise<{ items: AdminOrgConnector[] }> {
  const res = await client.get('/api/v1/admin/mcp/connectors')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: AdminOrgConnector[] }
}

export async function wsListAvailable(
  client: ApiClient,
  wsId: string,
): Promise<{ items: WsAvailable[] }> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/available`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: WsAvailable[] }
}

// ---------------- Active-tools registry (chat UI tool icons) ---------------- //

/** One MCP ``Icon`` (spec rev 2025-11-25). ``src`` is HTTP(S) URL or ``data:`` URI. */
export interface MCPToolIcon {
  src: string
  mime_type: string | null
  sizes: string[] | null
  /** ``"light"`` / ``"dark"`` when the server supplies separate variants. */
  theme: string | null
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
