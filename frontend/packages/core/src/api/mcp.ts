import type {
  CredentialStatus,
  CredentialUpsertBody,
  MCPCatalogConnector,
  MCPCatalogInstallRequest,
  MCPCatalogInstallResult,
  MCPCatalogInstallWsRequest,
  MCPCatalogListResponse,
  MCPInstallSwitchAuthRequest,
  MCPOAuthStartResult,
  MCPOrgInstallOverrideRequest,
  MCPOverrideUpdateBody,
  MCPServer,
  MCPServerCreateAdminBody,
  MCPServerCreateWSBody,
  MCPServerListWS,
  MCPServerPatchBody,
  MCPTestConnectionBody,
  MCPTestConnectionResult,
  PromoteBody,
  WorkspaceOverride,
} from '../types/mcp'
import { toApiError, type ApiClient } from './client'

interface AdminServerFilters {
  scope?: string
  owner_workspace_id?: string
  has_error?: boolean
}

function adminServerQuery(filters?: AdminServerFilters): string {
  const qs = new URLSearchParams()
  if (filters?.scope) qs.set('scope', filters.scope)
  if (filters?.owner_workspace_id) qs.set('owner_workspace_id', filters.owner_workspace_id)
  if (filters?.has_error !== undefined) qs.set('has_error', String(filters.has_error))
  const query = qs.toString()
  return query ? `?${query}` : ''
}

export async function adminListServers(
  client: ApiClient,
  filters?: AdminServerFilters,
): Promise<MCPServer[]> {
  const res = await client.get(`/api/v1/admin/mcp/servers${adminServerQuery(filters)}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer[]
}

export async function adminCreateServer(
  client: ApiClient,
  body: MCPServerCreateAdminBody,
): Promise<MCPServer> {
  const res = await client.post('/api/v1/admin/mcp/servers', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function adminGetServer(client: ApiClient, id: string): Promise<MCPServer> {
  const res = await client.get(`/api/v1/admin/mcp/servers/${id}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function adminPatchServer(
  client: ApiClient,
  id: string,
  body: MCPServerPatchBody,
): Promise<MCPServer> {
  const res = await client.patch(`/api/v1/admin/mcp/servers/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function adminDeleteServer(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/mcp/servers/${id}`)
  if (!res.ok) throw await toApiError(res)
}

export async function adminRefreshTools(client: ApiClient, id: string): Promise<MCPServer> {
  const res = await client.post(`/api/v1/admin/mcp/servers/${id}/refresh-tools`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function adminTestConnection(
  client: ApiClient,
  body: MCPTestConnectionBody,
): Promise<MCPTestConnectionResult> {
  const res = await client.post('/api/v1/admin/mcp/test-connection', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPTestConnectionResult
}

export async function adminGetOverrides(
  client: ApiClient,
  id: string,
): Promise<WorkspaceOverride[]> {
  const res = await client.get(`/api/v1/admin/mcp/servers/${id}/overrides`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WorkspaceOverride[]
}

export async function adminPutOverride(
  client: ApiClient,
  id: string,
  body: MCPOverrideUpdateBody,
): Promise<WorkspaceOverride[]> {
  const res = await client.put(`/api/v1/admin/mcp/servers/${id}/overrides`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WorkspaceOverride[]
}

export async function wsListServers(client: ApiClient, wsId: string): Promise<MCPServerListWS> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/servers`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServerListWS
}

export async function wsCreateServer(
  client: ApiClient,
  wsId: string,
  body: MCPServerCreateWSBody,
): Promise<MCPServer> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/servers`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function wsGetServer(client: ApiClient, wsId: string, id: string): Promise<MCPServer> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/servers/${id}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function wsPatchServer(
  client: ApiClient,
  wsId: string,
  id: string,
  body: MCPServerPatchBody,
): Promise<MCPServer> {
  const res = await client.patch(`/api/v1/ws/${wsId}/mcp/servers/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function wsDeleteServer(client: ApiClient, wsId: string, id: string): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/servers/${id}`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsRefreshTools(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<MCPServer> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/servers/${id}/refresh-tools`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function wsTestConnection(
  client: ApiClient,
  wsId: string,
  body: MCPTestConnectionBody,
): Promise<MCPTestConnectionResult> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/test-connection`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPTestConnectionResult
}

export async function wsPromote(
  client: ApiClient,
  wsId: string,
  id: string,
  body: PromoteBody,
): Promise<MCPServer> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/servers/${id}/promote-to-org`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPServer
}

export async function wsGetMyCredential(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<CredentialStatus> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as CredentialStatus
}

export async function wsPutMyCredential(
  client: ApiClient,
  wsId: string,
  id: string,
  body: CredentialUpsertBody,
): Promise<CredentialStatus> {
  const res = await client.put(`/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as CredentialStatus
}

export async function wsDeleteMyCredential(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/servers/${id}/my-credential`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsGetWorkspaceCredential(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<CredentialStatus> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as CredentialStatus
}

export async function wsPutWorkspaceCredential(
  client: ApiClient,
  wsId: string,
  id: string,
  body: CredentialUpsertBody,
): Promise<CredentialStatus> {
  const res = await client.put(`/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as CredentialStatus
}

export async function wsDeleteWorkspaceCredential(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/servers/${id}/workspace-credential`)
  if (!res.ok) throw await toApiError(res)
}

// ---------------- Catalog (workspace member) ---------------- //

interface CatalogListParams {
  q?: string
  provider?: string
}

function catalogQuery(params?: CatalogListParams): string {
  const qs = new URLSearchParams()
  if (params?.q) qs.set('q', params.q)
  if (params?.provider) qs.set('provider', params.provider)
  const query = qs.toString()
  return query ? `?${query}` : ''
}

export async function wsCatalogList(
  client: ApiClient,
  wsId: string,
  params?: CatalogListParams,
): Promise<MCPCatalogConnector[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/mcp/catalog${catalogQuery(params)}`)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as MCPCatalogListResponse
  return data.items
}

export async function wsCatalogInstall(
  client: ApiClient,
  wsId: string,
  catalogId: string,
  body: MCPCatalogInstallWsRequest,
): Promise<MCPCatalogInstallResult> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/catalog/${catalogId}/install`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCatalogInstallResult
}

export async function wsCatalogDeleteInstall(
  client: ApiClient,
  wsId: string,
  installId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/mcp/installs/${installId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsCatalogOverrideOrgInstall(
  client: ApiClient,
  wsId: string,
  installId: string,
  body: MCPOrgInstallOverrideRequest,
): Promise<void> {
  const res = await client.patch(`/api/v1/ws/${wsId}/mcp/org-installs/${installId}/override`, body)
  if (!res.ok) throw await toApiError(res)
}

export async function wsOAuthStart(
  client: ApiClient,
  wsId: string,
  installId: string,
): Promise<MCPOAuthStartResult> {
  const res = await client.post(`/api/v1/ws/${wsId}/mcp/installs/${installId}/oauth/start`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPOAuthStartResult
}

// ---------------- Catalog (admin) ---------------- //

export async function adminCatalogInstall(
  client: ApiClient,
  catalogId: string,
  body: MCPCatalogInstallRequest,
): Promise<MCPCatalogInstallResult> {
  const res = await client.post(`/api/v1/admin/mcp/catalog/${catalogId}/install`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCatalogInstallResult
}

export async function adminCatalogDeleteInstall(
  client: ApiClient,
  installId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/admin/mcp/installs/${installId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function adminCatalogPatchInstall(
  client: ApiClient,
  installId: string,
  body: MCPInstallSwitchAuthRequest,
): Promise<MCPCatalogInstallResult> {
  const res = await client.patch(`/api/v1/admin/mcp/installs/${installId}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPCatalogInstallResult
}

export async function adminOAuthStart(
  client: ApiClient,
  installId: string,
): Promise<MCPOAuthStartResult> {
  const res = await client.post(`/api/v1/admin/mcp/installs/${installId}/oauth/start`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MCPOAuthStartResult
}
