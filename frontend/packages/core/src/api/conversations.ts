import type { Artifact, ArtifactVersion, Conversation, Message } from '../types'
import { toApiError, type ApiClient } from './client'

export async function createConversation(
  client: ApiClient,
  title?: string
): Promise<Conversation> {
  const url = title
    ? `/api/v1/conversations?title=${encodeURIComponent(title)}`
    : '/api/v1/conversations'
  const res = await client.post(url, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function listConversations(
  client: ApiClient,
  limit = 50,
  offset = 0
): Promise<Conversation[]> {
  const url = `/api/v1/conversations?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json() as { conversations?: Conversation[] }
  return data.conversations || []
}

export async function getConversation(
  client: ApiClient,
  id: string
): Promise<Conversation> {
  const res = await client.get(`/api/v1/conversations/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function deleteConversation(
  client: ApiClient,
  id: string
): Promise<void> {
  const res = await client.post(`/api/v1/conversations/${id}?_method=DELETE`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function renameConversation(
  client: ApiClient,
  id: string,
  title: string
): Promise<Conversation> {
  const res = await client.post(`/api/v1/conversations/${id}?_method=PATCH`, {
    title,
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function listMessages(
  client: ApiClient,
  conversationId: string,
  limit = 50,
  offset = 0
): Promise<Message[]> {
  const url = `/api/v1/conversations/${conversationId}/messages?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json() as { messages?: Message[] }
  return data.messages || []
}

export async function listArtifacts(
  client: ApiClient,
  conversationId: string,
): Promise<Artifact[]> {
  const url = `/api/v1/conversations/${conversationId}/artifacts`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json() as { artifacts?: Artifact[] }
  return data.artifacts || []
}

export async function listArtifactVersions(
  client: ApiClient,
  conversationId: string,
  artifactId: string,
): Promise<ArtifactVersion[]> {
  const url = `/api/v1/conversations/${conversationId}/artifacts/${artifactId}/versions`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json() as { versions?: ArtifactVersion[] }
  return data.versions || []
}
