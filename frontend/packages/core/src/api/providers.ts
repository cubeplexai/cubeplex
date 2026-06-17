import type { ApiClient } from './client'
import { toApiError } from './client'
import type {
  Provider,
  ProviderCreate,
  ProviderUpdate,
  Model,
  ModelCreate,
  ModelUpdate,
  VendorPreset,
  ProbeStep,
  ProbeResult,
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

export async function listPresets(client: ApiClient): Promise<VendorPreset[]> {
  const res = await client.get('/api/v1/admin/llm/presets')
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<VendorPreset[]>
}

interface LivenessBody {
  api: string
  base_url: string
  api_key?: string | null
  capability: Record<string, unknown>
  model_capability_overrides?: Record<string, unknown>
  model_id: string
}

export async function presaveLiveness(client: ApiClient, body: LivenessBody): Promise<ProbeStep> {
  const res = await client.post('/api/v1/admin/providers/liveness', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ProbeStep>
}

export async function presaveTest(client: ApiClient, body: LivenessBody): Promise<ProbeResult> {
  const res = await client.post('/api/v1/admin/providers/test', body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ProbeResult>
}

export async function checkLiveness(
  client: ApiClient,
  providerId: string,
  modelId: string,
): Promise<ProbeStep> {
  const res = await client.post(`/api/v1/admin/providers/${providerId}/liveness`, {
    model_id: modelId,
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ProbeStep>
}

export async function testModel(
  client: ApiClient,
  providerId: string,
  modelDbId: string,
): Promise<ProbeResult> {
  const res = await client.post(
    `/api/v1/admin/providers/${providerId}/models/${modelDbId}/test`,
    {},
  )
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ProbeResult>
}

export async function setModelEnabled(
  client: ApiClient,
  providerId: string,
  modelDbId: string,
  enabled: boolean,
): Promise<Model> {
  const res = await client.patch(`/api/v1/admin/providers/${providerId}/models/${modelDbId}`, {
    enabled,
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Model>
}
