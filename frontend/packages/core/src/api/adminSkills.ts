import { toApiError } from './client'
import type { SkillCandidateOut, SkillPreviewResponse } from './skills'

export async function adminDiscoverSkills(q: string, limit = 10): Promise<SkillCandidateOut[]> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  const res = await fetch(`/api/v1/admin/skills/discover?${params}`, { credentials: 'include' })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillCandidateOut[]
}

export async function adminPreviewCandidate(candidateId: string): Promise<SkillPreviewResponse> {
  const params = new URLSearchParams({ candidate_id: candidateId })
  const res = await fetch(`/api/v1/admin/skills/discover/preview?${params}`, {
    credentials: 'include',
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SkillPreviewResponse
}

export async function adminInstallCandidate(
  candidateId: string,
  extraHeaders?: HeadersInit,
): Promise<{ canonical_name: string; skill_id: string; installed_version: string }> {
  const res = await fetch('/api/v1/admin/skills/install-candidate', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...Object.fromEntries(new Headers(extraHeaders)),
    },
    body: JSON.stringify({ candidate_id: candidateId }),
  })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<{
    canonical_name: string
    skill_id: string
    installed_version: string
  }>
}
