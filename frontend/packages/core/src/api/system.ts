import { toApiError, type ApiClient } from './client'

export interface SystemInfoResponse {
  deployment_mode: 'single_tenant' | 'multi_tenant'
  version: string
  needs_org_setup: boolean
  sandbox_enabled?: boolean
}

export interface SetupRequest {
  org_name: string
  slug: string
}

export interface SetupResponse {
  org_id: string
  workspace_id: string
}

export async function fetchSystemInfo(client: ApiClient): Promise<SystemInfoResponse> {
  const res = await client.get('/api/v1/system/info')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SystemInfoResponse
}

export async function postSetup(client: ApiClient, body: SetupRequest): Promise<SetupResponse> {
  const res = await client.post('/api/v1/system/setup', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SetupResponse
}
