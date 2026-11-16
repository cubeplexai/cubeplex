import { toApiError, type ApiClient } from './client'
import type { HardDeleteResult, LeaveWorkspaceResult } from './execution'

export type WorkspaceSandboxMode = 'per_user' | 'workspace'

export interface Workspace {
  id: string
  name: string
  org_id: string
  role?: 'admin' | 'member'
  sandbox_mode?: WorkspaceSandboxMode
  /** ISO-8601 with UTC offset, or null if the workspace has no conversations yet. */
  last_activity_at?: string | null
}

export interface CreateWorkspaceInput {
  name: string
  orgId: string
  sandboxMode?: WorkspaceSandboxMode
}

export async function listWorkspaces(client: ApiClient): Promise<Workspace[]> {
  const res = await client.get('/api/v1/workspaces')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace[]
}

export async function createWorkspace(
  client: ApiClient,
  input: CreateWorkspaceInput,
): Promise<Workspace> {
  const body: { name: string; org_id: string; sandbox_mode?: WorkspaceSandboxMode } = {
    name: input.name,
    org_id: input.orgId,
  }
  if (input.sandboxMode !== undefined) {
    body.sandbox_mode = input.sandboxMode
  }
  const res = await client.post('/api/v1/workspaces', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace
}

export async function renameWorkspace(
  client: ApiClient,
  wsId: string,
  name: string,
): Promise<Workspace> {
  const res = await client.patch(`/api/v1/workspaces/${wsId}`, { name })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace
}

export async function leaveWorkspace(
  client: ApiClient,
  wsId: string,
): Promise<LeaveWorkspaceResult> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/leave`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as LeaveWorkspaceResult
}

export async function archiveWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/archive`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function unarchiveWorkspace(client: ApiClient, wsId: string): Promise<void> {
  const res = await client.post(`/api/v1/workspaces/${wsId}/unarchive`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function deleteWorkspace(client: ApiClient, wsId: string): Promise<HardDeleteResult> {
  const res = await client.del(`/api/v1/workspaces/${wsId}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as HardDeleteResult
}
