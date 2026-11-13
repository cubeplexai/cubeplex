import { toApiError, type ApiClient } from './client'

export interface OrgInviteOut {
  token: string
  expires_at: string
  role: string
  invite_url: string
  email: string | null
  email_sent: boolean
}

export interface AcceptOrgInviteResult {
  org_id: string
  role: string
}

export async function createOrgInvite(
  client: ApiClient,
  role: 'admin' | 'member',
  email?: string,
): Promise<OrgInviteOut> {
  const res = await client.post('/api/v1/admin/orgs/invites', {
    role,
    ...(email ? { email } : {}),
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OrgInviteOut
}

export async function acceptOrgInvite(
  client: ApiClient,
  token: string,
): Promise<AcceptOrgInviteResult> {
  const res = await client.post('/api/v1/orgs/invites/accept', { token })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AcceptOrgInviteResult
}
