import type { ApiClient } from './client'
import { toApiError } from './client'
import type { ConversationShare, PublicShare, ShareScope } from '../types/share'

export async function createShare(
  client: ApiClient,
  conversationId: string,
  scope: ShareScope = 'public',
): Promise<ConversationShare> {
  const res = await client.post('/api/v1/shares', {
    conversation_id: conversationId,
    scope,
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ConversationShare>
}

export async function listConversationShares(
  client: ApiClient,
  conversationId: string,
): Promise<ConversationShare[]> {
  const res = await client.get(`/api/v1/shares/conversation/${conversationId}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ConversationShare[]>
}

export async function listShares(
  client: ApiClient,
  limit = 50,
  offset = 0,
): Promise<{ items: ConversationShare[]; total: number }> {
  const res = await client.get(`/api/v1/shares?limit=${limit}&offset=${offset}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<{ items: ConversationShare[]; total: number }>
}

export async function revokeShare(client: ApiClient, shareId: string): Promise<ConversationShare> {
  const res = await client.patch(`/api/v1/shares/${shareId}`, { is_active: false })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ConversationShare>
}

export async function getPublicShare(shareId: string): Promise<PublicShare> {
  const res = await fetch(`/api/v1/shares/${shareId}`)
  if (!res.ok) {
    throw new Error(res.status === 404 ? 'Share not found' : 'Failed to load share')
  }
  return res.json() as Promise<PublicShare>
}
