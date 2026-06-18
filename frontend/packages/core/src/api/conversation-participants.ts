import type { Conversation, ConversationParticipant } from '../types'
import { toApiError, type ApiClient } from './client'

export async function inviteToGroup(
  client: ApiClient,
  conversationId: string,
  userIds: string[],
): Promise<{ participants: ConversationParticipant[]; conversation: Conversation }> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/invite-to-group`, {
    user_ids: userIds,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as {
    participants: ConversationParticipant[]
    conversation: Conversation
  }
}

export async function listConversationParticipants(
  client: ApiClient,
  conversationId: string,
): Promise<{ items: ConversationParticipant[] }> {
  const res = await client.get(`/api/v1/conversations/${conversationId}/participants`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { items: ConversationParticipant[] }
}
