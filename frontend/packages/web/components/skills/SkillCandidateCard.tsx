'use client'

import { useMemo } from 'react'
import { createApiClient, useSkillsStore, type SkillCandidateOut } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export function SkillCandidateCard({
  wsId,
  candidate,
}: {
  wsId: string
  candidate: SkillCandidateOut
}) {
  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const install = useSkillsStore((s) => s.install)
  const installing = useSkillsStore((s) => s.installing[candidate.candidate_id] ?? false)

  return (
    <div data-testid="skill-candidate-card" className="rounded-lg border p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{candidate.name}</div>
          <div className="text-muted-foreground text-xs">{candidate.canonical_name}</div>
          <p className="mt-1 text-sm">{candidate.description}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            <Badge variant="secondary">{candidate.source_name}</Badge>
            {candidate.unvetted && <Badge variant="destructive">unvetted</Badge>}
            {candidate.repo && (
              <span className="text-muted-foreground text-xs truncate">{candidate.repo}</span>
            )}
          </div>
        </div>
        <Button
          size="sm"
          disabled={installing || candidate.install_state === 'enabled'}
          onClick={() => void install(client, wsId, candidate.candidate_id)}
        >
          {candidate.install_state === 'enabled' ? 'Installed' : 'Install'}
        </Button>
      </div>
    </div>
  )
}
