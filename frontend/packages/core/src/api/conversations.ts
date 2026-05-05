import type { Artifact, ArtifactVersion, Conversation, Message } from '../types'
import { toApiError, type ApiClient } from './client'

export async function createConversation(
  client: ApiClient,
  title?: string,
  opts: { draft?: boolean } = {},
): Promise<Conversation> {
  const params = new URLSearchParams()
  if (title) params.set('title', title)
  if (opts.draft) params.set('draft', 'true')
  const qs = params.toString()
  const url = qs ? `/api/v1/conversations?${qs}` : '/api/v1/conversations'
  const res = await client.post(url, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function listConversations(
  client: ApiClient,
  limit = 50,
  offset = 0,
): Promise<Conversation[]> {
  const url = `/api/v1/conversations?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { conversations?: Conversation[] }
  return data.conversations || []
}

export async function getConversation(client: ApiClient, id: string): Promise<Conversation> {
  const res = await client.get(`/api/v1/conversations/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function deleteConversation(client: ApiClient, id: string): Promise<void> {
  // Backend route is `@router.delete("/{conversation_id}")`. There is no
  // method-override middleware, so we call DELETE directly.
  const res = await client.del(`/api/v1/conversations/${id}`)
  if (!res.ok) throw await toApiError(res)
}

export async function renameConversation(
  client: ApiClient,
  id: string,
  title: string,
): Promise<Conversation> {
  // Backend route is `@router.patch("/{conversation_id}")` with `title` as a
  // query parameter, not a body. There is no method-override middleware,
  // so we call PATCH directly.
  const res = await client.patch(
    `/api/v1/conversations/${id}?title=${encodeURIComponent(title)}`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}

export async function listMessages(
  client: ApiClient,
  conversationId: string,
  limit = 50,
  offset = 0,
): Promise<Message[]> {
  const url = `/api/v1/conversations/${conversationId}/messages?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { messages?: Message[] }
  return data.messages || []
}

export async function listArtifacts(
  client: ApiClient,
  conversationId: string,
): Promise<Artifact[]> {
  const url = `/api/v1/conversations/${conversationId}/artifacts`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = (await res.json()) as { artifacts?: Artifact[] }
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
  const data = (await res.json()) as { versions?: ArtifactVersion[] }
  return data.versions || []
}
