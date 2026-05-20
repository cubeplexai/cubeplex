import type { ApiClient } from './client'
import { toApiError } from './client'
import type {
  Provider,
  ProviderCreate,
  ProviderUpdate,
  Model,
  ModelCreate,
  ModelUpdate,
  OrgLLMSettings,
  OrgLLMSettingsUpdate,
} from '../types/provider'

export async function fetchProviders(client: ApiClient): Promise<Provider[]> {
  const res = await client.get('/api/v1/admin/providers')
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider[]>
}

export async function fetchProvider(client: ApiClient, id: string): Promise<Provider> {
  const res = await client.get(`/api/v1/admin/providers/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider>
}

export async function createProvider(client: ApiClient, body: ProviderCreate): Promise<Provider> {
  const res = await client.post('/api/v1/admin/providers', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider>
}

export async function updateProvider(
  client: ApiClient,
  id: string,
  body: ProviderUpdate,
): Promise<Provider> {
  const res = await client.patch(`/api/v1/admin/providers/${id}`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Provider>
}

export async function deleteProvider(client: ApiClient, id: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/providers/${id}`)
  if (!res.ok) throw await toApiError(res)
}

export async function createModel(
  client: ApiClient,
  providerId: string,
  body: ModelCreate,
): Promise<Model> {
  const res = await client.post(`/api/v1/admin/providers/${providerId}/models`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Model>
}

export async function updateModel(
  client: ApiClient,
  providerId: string,
  modelId: string,
  body: ModelUpdate,
): Promise<Model> {
  const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${modelId}`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Model>
}

export async function deleteModel(
  client: ApiClient,
  providerId: string,
  modelId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/admin/providers/${providerId}/models/${modelId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function fetchOrgLLMSettings(client: ApiClient): Promise<OrgLLMSettings> {
  const res = await client.get('/api/v1/admin/settings/llm')
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<OrgLLMSettings>
}

export async function updateOrgLLMSettings(
  client: ApiClient,
  body: OrgLLMSettingsUpdate,
): Promise<OrgLLMSettings> {
  const res = await client.put('/api/v1/admin/settings/llm', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<OrgLLMSettings>
}
