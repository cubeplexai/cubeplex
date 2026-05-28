'use client'

import { useMemo, useState } from 'react'
import { createApiClient, useSkillsStore } from '@cubebox/core'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { SkillCandidateCard } from './SkillCandidateCard'

export function DiscoverPanel({ wsId }: { wsId: string }) {
  const [q, setQ] = useState('')

  const client = useMemo(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  const search = useSkillsStore((s) => s.search)
  const candidates = useSkillsStore((s) => s.candidates)
  const lastInstalled = useSkillsStore((s) => s.lastInstalled)

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search skills"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && q.trim()) void search(client, wsId, q)
          }}
          className="max-w-md"
        />
        <Button onClick={() => void search(client, wsId, q)} disabled={!q.trim()}>
          Search
        </Button>
      </div>

      {lastInstalled && (
        <div className="rounded-md bg-emerald-50 px-3 py-2 text-sm dark:bg-emerald-950/20">
          Installed {lastInstalled.canonical_name} (v{lastInstalled.version}). Use in conversation
          with <code>load_skill(&quot;{lastInstalled.canonical_name}&quot;)</code>.
        </div>
      )}

      <div className="grid gap-3">
        {candidates.map((c) => (
          <SkillCandidateCard key={c.candidate_id} wsId={wsId} candidate={c} />
        ))}
      </div>
    </section>
  )
}
