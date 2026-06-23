import { toApiError, type ApiClient } from './client'

export interface ApiKeyListItem {
  id: string
  label: string
  prefix: string
  last_used_at: string | null
  created_at: string
}

export interface ApiKeyCreated {
  id: string
  label: string
  prefix: string
  created_at: string
  /** Plaintext token. Available ONLY on create — never returned again. */
  token: string
}

export async function listApiKeys(client: ApiClient): Promise<ApiKeyListItem[]> {
  const res = await client.get('/api/v1/me/api-keys')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ApiKeyListItem[]
}

export async function createApiKey(client: ApiClient, label: string): Promise<ApiKeyCreated> {
  const res = await client.post('/api/v1/me/api-keys', { label })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ApiKeyCreated
}

export async function deleteApiKey(client: ApiClient, keyId: string): Promise<void> {
  const res = await client.del(`/api/v1/me/api-keys/${keyId}`)
  if (!res.ok && res.status !== 404) throw await toApiError(res)
}
