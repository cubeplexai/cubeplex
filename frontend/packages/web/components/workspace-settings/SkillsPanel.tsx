'use client'

import { useCallback, useEffect, useState } from 'react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import type { SkillInstall } from '@cubebox/core'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface SkillsPanelProps {
  wsId: string
}

export function SkillsPanel({ wsId }: SkillsPanelProps) {
  const { skills, loading, loadAll, toggleSkill } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<SkillInstall | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!skills) loadAll(client())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  const allSkills = [...(skills?.org_skills ?? []), ...(skills?.workspace_skills ?? [])]

  const handleToggle = async (skill: SkillInstall, enabled: boolean) => {
    if (skill.scope === 'workspace') return
    setToggling(skill.install_id)
    try {
      await toggleSkill(client(), skill.install_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Col 2: list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-3 border-b border-border">
          <p className="text-sm font-semibold">Skills</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {allSkills.filter((s) => s.enabled).length} / {allSkills.length} enabled
          </p>
        </div>
        <ul className="p-2 space-y-0.5">
          {loading && !skills ? (
            <li className="text-xs text-muted-foreground px-2 py-4">Loading…</li>
          ) : allSkills.length === 0 ? (
            <li className="text-xs text-muted-foreground px-2 py-4">No skills available</li>
          ) : (
            allSkills.map((skill) => (
              <li key={skill.install_id}>
                <button
                  onClick={() => setSelected(skill)}
                  className={cn(
                    'w-full flex items-center gap-2 px-2 py-2 rounded-md text-left transition-colors',
                    selected?.install_id === skill.install_id
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
                  )}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-[12px] font-medium truncate">{skill.skill_id}</p>
                    <p className="text-[10px] text-muted-foreground/60">
                      {skill.installed_version}
                    </p>
                  </div>
                  <Switch
                    checked={skill.enabled}
                    disabled={skill.scope === 'workspace' || toggling === skill.install_id}
                    onCheckedChange={(v) => handleToggle(skill, v)}
                    onClick={(e) => e.stopPropagation()}
                    className="shrink-0 scale-75"
                  />
                </button>
              </li>
            ))
          )}
        </ul>
      </div>

      {/* Col 3: detail */}
      <div className="flex-1 overflow-y-auto p-8">
        {selected ? (
          <>
            <h2 className="text-base font-semibold mb-1">{selected.skill_id}</h2>
            <div className="flex gap-2 mb-6">
              <Badge variant="outline">{selected.installed_version}</Badge>
              <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'outline'}>
                {selected.scope === 'workspace' ? 'workspace-private' : 'org-installed'}
              </Badge>
              <Badge variant={selected.enabled ? 'default' : 'secondary'}>
                {selected.enabled ? 'enabled' : 'disabled'}
              </Badge>
            </div>
            <div className="space-y-3 text-sm text-muted-foreground">
              <div className="flex justify-between py-2 border-b border-border">
                <span>Install ID</span>
                <span className="font-mono text-xs">{selected.install_id}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Scope</span>
                <span>{selected.scope}</span>
              </div>
              <div className="flex justify-between py-2 border-b border-border">
                <span>Version</span>
                <span>{selected.installed_version}</span>
              </div>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a skill to view details</p>
        )}
      </div>
    </div>
  )
}
