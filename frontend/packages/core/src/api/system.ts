import { toApiError, type ApiClient } from './client'

export interface SystemInfoResponse {
  deployment_mode: 'single_tenant' | 'multi_tenant'
  version: string
  sandbox_enabled?: boolean
  password_policy?: 'low' | 'high'
}

export async function fetchSystemInfo(client: ApiClient): Promise<SystemInfoResponse> {
  const res = await client.get('/api/v1/system/info')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SystemInfoResponse
}
