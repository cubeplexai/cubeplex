import { toApiError, type ApiClient } from './client'

export interface SkillCandidateOut {
  candidate_id: string
  name: string
  canonical_name: string
  description: string
  source_kind: 'local' | 'remote'
  keywords: string[]
  version: string | null
  trust: 'official' | 'community' | 'untrusted'
  install_state: 'enabled' | 'in_catalog' | 'available'
  stars: number | null
  install_count: number | null
  source_name: string
  repo: string | null
  unvetted: boolean
}

export type SkillCandidateListResponse = SkillCandidateOut[]

export interface SkillPreviewResponse {
  content: string
  env_vars: string[]
}

export interface SkillInstallResponse {
  canonical_name: string
  skill_id: string
  installed_version: string
}

export interface SkillRefreshResponse {
  canonical_name: string
  skill_id: string
  installed_version: string
  /** False when re-import produced no new version. */
  changed: boolean
}

export async function discoverSkills(
  client: ApiClient,
  wsId: string,
  q: string,
  limit = 5,
): Promise<SkillCandidateListResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  const res = await client.get(`/api/v1/ws/${wsId}/skills/discover?${params}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillCandidateListResponse
}

export async function installSkill(
  client: ApiClient,
  wsId: string,
  candidateId: string,
): Promise<SkillInstallResponse> {
  const res = await client.post(`/api/v1/ws/${wsId}/skills/install`, {
    candidate_id: candidateId,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillInstallResponse
}

export async function refreshSkill(
  client: ApiClient,
  wsId: string,
  skillId: string,
): Promise<SkillRefreshResponse> {
  const res = await client.post(`/api/v1/ws/${wsId}/skills/${skillId}/refresh`, null)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillRefreshResponse
}
