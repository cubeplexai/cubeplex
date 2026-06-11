import { toApiError, type ApiClient } from './client'

export interface InviteToken {
  token: string
  role: string
  created_by: string
  expires_at: string
  used_at: string | null
}

export interface AcceptInviteResult {
  workspace_id: string
  workspace_name: string
  org_id: string
  role: string
}

export async function createInvite(
  client: ApiClient,
  wsId: string,
  role: string,
  email?: string,
): Promise<{ token: string; expires_at: string; email_sent: boolean }> {
  const body: Record<string, string> = { role }
  if (email) body.email = email
  const res = await client.post(`/api/v1/workspaces/${wsId}/invites`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { token: string; expires_at: string; email_sent: boolean }
}

export async function listInvites(client: ApiClient, wsId: string): Promise<InviteToken[]> {
  const res = await client.get(`/api/v1/workspaces/${wsId}/invites`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as InviteToken[]
}

export async function revokeInvite(client: ApiClient, wsId: string, token: string): Promise<void> {
  const res = await client.del(`/api/v1/workspaces/${wsId}/invites/${token}`)
  if (!res.ok) throw await toApiError(res)
}

export async function acceptInvite(client: ApiClient, token: string): Promise<AcceptInviteResult> {
  const res = await client.post('/api/v1/workspaces/invites/accept', { token })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AcceptInviteResult
}
