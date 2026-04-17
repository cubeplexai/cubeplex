import { toApiError, type ApiClient } from './client'

export interface Workspace {
  id: string
  name: string
  org_id: string
  role?: 'admin' | 'member'
}

export async function listWorkspaces(client: ApiClient): Promise<Workspace[]> {
  const res = await client.get('/api/v1/workspaces')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace[]
}

export async function createWorkspace(
  client: ApiClient,
  input: { name: string; orgId: string },
): Promise<Workspace> {
  const res = await client.post('/api/v1/workspaces', { name: input.name, org_id: input.orgId })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as Workspace
}
