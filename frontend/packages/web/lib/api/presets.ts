import { jsonHeaders, readApiError } from '@/lib/csrf'
import type { ModelPresetsConfig, WorkspacePresetSummary } from '@/lib/types/presets'

export interface AdminModelPresetsResponse {
  value: ModelPresetsConfig | null
  origin: 'org' | 'system' | 'none'
}

export async function fetchAdminModelPresets(): Promise<AdminModelPresetsResponse> {
  const res = await fetch('/api/v1/admin/model-presets', { credentials: 'include' })
  if (!res.ok) throw new Error(await readApiError(res))
  return res.json()
}

export async function putAdminModelPresets(body: ModelPresetsConfig): Promise<void> {
  const res = await fetch('/api/v1/admin/model-presets', {
    method: 'PUT',
    credentials: 'include',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readApiError(res))
}

export async function fetchWorkspaceModelPresets(wsId: string): Promise<WorkspacePresetSummary[]> {
  const res = await fetch(`/api/v1/ws/${wsId}/model-presets`, { credentials: 'include' })
  if (!res.ok) throw new Error(await readApiError(res))
  const data = (await res.json()) as { presets: WorkspacePresetSummary[] }
  return data.presets
}
