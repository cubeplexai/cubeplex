'use client'

// Renders the workspace-enabled skills using the existing
// GET /api/v1/ws/{ws}/skills?scope=workspace endpoint (already proxied).
// "Check for update" is scoped-out for v1: the backend SkillSummary does not
// carry source_ref, so we can't surface the refresh button yet.
import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'

interface EnabledSkill {
  id: string
  name: string
  source: 'preinstalled' | 'uploaded'
  description: string
  current_version: string
  keywords: string[]
}

export function SkillsList({ wsId }: { wsId: string }) {
  const [rows, setRows] = useState<EnabledSkill[]>([])

  useEffect(() => {
    void (async () => {
      const r = await fetch(`/api/v1/ws/${wsId}/skills?scope=workspace`, {
        credentials: 'include',
      })
      if (r.ok) {
        setRows((await r.json()) as EnabledSkill[])
      }
    })()
  }, [wsId])

  return (
    <section className="flex flex-col gap-2" data-testid="skills-list">
      <h2 className="text-lg font-medium">Installed in this workspace</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No skills installed yet. Search above to discover and install skills.
        </p>
      ) : (
        <ul className="divide-y rounded-lg border">
          {rows.map((s) => (
            <li key={s.id} className="flex items-center justify-between px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{s.name}</div>
                <div className="flex gap-1 mt-1">
                  <Badge variant="secondary">{s.source}</Badge>
                  {s.keywords.slice(0, 3).map((k) => (
                    <Badge key={k} variant="outline" className="text-xs">
                      {k}
                    </Badge>
                  ))}
                </div>
              </div>
              <span className="text-xs text-muted-foreground ml-4">v{s.current_version}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
