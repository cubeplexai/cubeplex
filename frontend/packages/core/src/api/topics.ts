import type { ApiClient } from './client'
import type { SandboxStatusOut } from './sandboxPolicy'
import type {
  Topic,
  TopicCreateResponse,
  TopicDetailResponse,
  TopicParticipant,
} from '../types/topic'

export async function createTopic(
  client: ApiClient,
  body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
): Promise<TopicCreateResponse> {
  const res = await client.post('/api/v1/topics', body)
  return await res.json()
}

export async function listTopics(client: ApiClient): Promise<{ items: Topic[] }> {
  const res = await client.get('/api/v1/topics')
  return await res.json()
}

export async function getTopic(client: ApiClient, topicId: string): Promise<TopicDetailResponse> {
  const res = await client.get(`/api/v1/topics/${topicId}`)
  return await res.json()
}

export async function updateTopic(
  client: ApiClient,
  topicId: string,
  body: { title?: string },
): Promise<{ topic: Topic }> {
  const res = await client.patch(`/api/v1/topics/${topicId}`, body)
  return await res.json()
}

export async function deleteTopic(client: ApiClient, topicId: string): Promise<void> {
  await client.del(`/api/v1/topics/${topicId}`)
}

export async function addTopicParticipants(
  client: ApiClient,
  topicId: string,
  userIds: string[],
): Promise<{ participants: TopicParticipant[] }> {
  const res = await client.post(`/api/v1/topics/${topicId}/participants`, {
    user_ids: userIds,
  })
  return await res.json()
}

export async function removeTopicParticipant(
  client: ApiClient,
  topicId: string,
  userId: string,
): Promise<void> {
  await client.del(`/api/v1/topics/${topicId}/participants/${userId}`)
}

export async function updateParticipantRole(
  client: ApiClient,
  topicId: string,
  userId: string,
  role: 'owner' | 'member',
): Promise<{ participant: TopicParticipant }> {
  const res = await client.patch(`/api/v1/topics/${topicId}/participants/${userId}`, { role })
  return await res.json()
}

export async function createTopicConversation(
  client: ApiClient,
  topicId: string,
  title?: string,
): Promise<{ conversation: { id: string; title: string; topic_id: string } }> {
  const res = await client.post(`/api/v1/topics/${topicId}/conversations`, {
    title: title ?? null,
  })
  return await res.json()
}

export async function upgradeToTopic(
  client: ApiClient,
  conversationId: string,
  body: { title: string; sandbox_mode?: string; member_user_ids?: string[] },
): Promise<TopicCreateResponse> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/upgrade-to-topic`, body)
  return await res.json()
}

export async function getTopicSandbox(
  client: ApiClient,
  topicId: string,
): Promise<SandboxStatusOut> {
  const res = await client.get(`/api/v1/topics/${topicId}/sandbox`)
  return await res.json()
}
