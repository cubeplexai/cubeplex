'use client'

import { SkillCandidateCard, type SkillCandidate } from './SkillCandidateCard'

interface FindSkillsPayload {
  candidates: SkillCandidate[]
  hint?: string
}

function parsePayload(json: string): SkillCandidate[] {
  try {
    const parsed = JSON.parse(json) as FindSkillsPayload
    return Array.isArray(parsed.candidates) ? parsed.candidates : []
  } catch {
    return []
  }
}

export function SkillSearchResults({ resultJson }: { resultJson: string }) {
  const candidates = parsePayload(resultJson)
  if (candidates.length === 0) return null

  return (
    <div className="flex flex-col gap-2 py-1">
      {candidates.map((c) => (
        <SkillCandidateCard key={c.candidate_id} candidate={c} />
      ))}
    </div>
  )
}
