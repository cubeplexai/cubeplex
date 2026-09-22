import { toApiError, type ApiClient } from './client'
import type { AccessRemovalResult } from './execution'

export interface OrgMember {
  user_id: string
  email: string
  display_name: string | null
  role: 'owner' | 'admin' | 'member'
  created_at: string
}

export interface WsMember {
  user_id: string
  email: string
  display_name: string | null
  role: 'admin' | 'member'
  created_at: string
}

export interface AvailableMember {
  user_id: string
  email: string
  org_role: string
}

export interface WorkspaceAccessRequest {
  id: string
  user_id: string
  email: string
  display_name: string | null
  created_at: string
}

export async function listWorkspaceAccessRequests(
  client: ApiClient,
  wsId: string,
): Promise<WorkspaceAccessRequest[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/members/access-requests`)
  if (!res.ok) throw await toApiError(res)
  return ((await res.json()) as { requests: WorkspaceAccessRequest[] }).requests
}

export async function resolveWorkspaceAccessRequest(
  client: ApiClient,
  wsId: string,
  requestId: string,
  approved: boolean,
): Promise<void> {
  const action = approved ? 'approve' : 'reject'
  const res = await client.post(
    `/api/v1/ws/${wsId}/members/access-requests/${requestId}/${action}`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
}

export async function listOrgMembers(client: ApiClient): Promise<OrgMember[]> {
  const res = await client.get('/api/v1/admin/members')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OrgMember[]
}

export async function addOrgMember(
  client: ApiClient,
  email: string,
  role: string,
): Promise<{ user_id: string; email: string; role: string }> {
  const res = await client.post('/api/v1/admin/members', { email, role })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { user_id: string; email: string; role: string }
}

export async function updateOrgMemberRole(
  client: ApiClient,
  userId: string,
  role: string,
): Promise<void> {
  const res = await client.patch(`/api/v1/admin/members/${userId}/role`, { role })
  if (!res.ok) throw await toApiError(res)
}

export async function removeOrgMember(
  client: ApiClient,
  userId: string,
): Promise<AccessRemovalResult> {
  const res = await client.del(`/api/v1/admin/members/${userId}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AccessRemovalResult
}

export async function listWsMembers(client: ApiClient, wsId: string): Promise<WsMember[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/members`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WsMember[]
}

export async function listAvailableMembers(
  client: ApiClient,
  wsId: string,
): Promise<AvailableMember[]> {
  const res = await client.get(`/api/v1/ws/${wsId}/members/available`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AvailableMember[]
}

export async function addWsMember(
  client: ApiClient,
  wsId: string,
  userId: string,
  role: string,
): Promise<{ user_id: string; email: string; role: string }> {
  const res = await client.post(`/api/v1/ws/${wsId}/members`, { user_id: userId, role })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { user_id: string; email: string; role: string }
}

export async function updateWsMemberRole(
  client: ApiClient,
  wsId: string,
  userId: string,
  role: string,
): Promise<void> {
  const res = await client.patch(`/api/v1/ws/${wsId}/members/${userId}/role`, { role })
  if (!res.ok) throw await toApiError(res)
}

export async function removeWsMember(
  client: ApiClient,
  wsId: string,
  userId: string,
): Promise<AccessRemovalResult> {
  const res = await client.del(`/api/v1/ws/${wsId}/members/${userId}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AccessRemovalResult
}
