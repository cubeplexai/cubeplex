import { toApiError, type ApiClient } from './client'

export interface EnvEntryOut {
  id: string
  env_name: string
  is_secret: boolean
  scope: 'org' | 'workspace' | 'user'
  workspace_id: string | null
  user_id: string | null
  hosts: string[] | null
  header_names?: string[] | null
  status: string
  warnings: string[]
}

export interface EnvEntryListOut {
  entries: EnvEntryOut[]
}

export interface CreateEnvIn {
  env_name: string
  is_secret: boolean
  hosts?: string[] | null
  secret_value?: string | null
  plain_value?: string | null
}

export interface UpdateEntryIn {
  secret_value?: string | null
  hosts?: string[] | null
  header_names?: string[] | null
}

// ── Workspace: workspace scope (/workspace) ──────────────────────────────────

export async function listWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
): Promise<EnvEntryListOut> {
  const res = await client.get(`/api/v1/ws/${wsId}/sandbox-env/workspace`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  body: CreateEnvIn,
): Promise<EnvEntryOut> {
  const res = await client.post(`/api/v1/ws/${wsId}/sandbox-env/workspace`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function updateWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  id: string,
  body: UpdateEntryIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/ws/${wsId}/sandbox-env/workspace/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteWsEnvWorkspace(
  client: ApiClient,
  wsId: string,
  id: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/sandbox-env/workspace/${id}`)
  if (!res.ok) throw await toApiError(res)
}

// ── Workspace: user scope (/me) ───────────────────────────────────────────────

export async function listWsEnvMe(client: ApiClient, wsId: string): Promise<EnvEntryListOut> {
  const res = await client.get(`/api/v1/ws/${wsId}/sandbox-env/me`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createWsEnvMe(
  client: ApiClient,
  wsId: string,
  body: CreateEnvIn,
): Promise<EnvEntryOut> {
  const res = await client.post(`/api/v1/ws/${wsId}/sandbox-env/me`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function updateWsEnvMe(
  client: ApiClient,
  wsId: string,
  id: string,
  body: UpdateEntryIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/ws/${wsId}/sandbox-env/me/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteWsEnvMe(client: ApiClient, wsId: string, id: string): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/sandbox-env/me/${id}`)
  if (!res.ok) throw await toApiError(res)
}

// ── Admin: org scope ──────────────────────────────────────────────────────────

export async function listAdminEnv(client: ApiClient): Promise<EnvEntryListOut> {
  const res = await client.get('/api/v1/admin/sandbox-env')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryListOut
}

export async function createAdminEnv(client: ApiClient, body: CreateEnvIn): Promise<EnvEntryOut> {
  const res = await client.post('/api/v1/admin/sandbox-env', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function updateAdminEnv(
  client: ApiClient,
  id: string,
  body: UpdateEntryIn,
): Promise<EnvEntryOut> {
  const res = await client.patch(`/api/v1/admin/sandbox-env/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as EnvEntryOut
}

export async function deleteAdminEnv(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/sandbox-env/${id}`)
  if (!res.ok) throw await toApiError(res)
}
