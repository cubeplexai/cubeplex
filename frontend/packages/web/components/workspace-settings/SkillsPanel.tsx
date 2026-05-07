'use client'

import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, Package, Plus, Sparkles } from 'lucide-react'
import { createApiClient, useWorkspaceSettingsStore } from '@cubebox/core'
import type { SkillInstall } from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { AddSkillModal } from './AddSkillModal'

interface SkillsPanelProps {
  wsId: string
}

function SkillItemCard({
  skill,
  active,
  toggling,
  onClick,
  onToggle,
}: {
  skill: SkillInstall
  active: boolean
  toggling: boolean
  onClick: () => void
  onToggle: (enabled: boolean) => void
}) {
  const SourceIcon = skill.scope === 'workspace' ? Package : Sparkles
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'true' : undefined}
      className={cn(
        'group flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-all',
        active
          ? 'border-primary/40 bg-primary/5 shadow-sm'
          : 'border-border/70 bg-card/40 hover:border-border hover:bg-accent/40',
      )}
    >
      <div className="flex items-center gap-2">
        <SourceIcon
          className={cn(
            'size-3.5 shrink-0',
            skill.scope === 'workspace' ? 'text-muted-foreground' : 'text-primary',
          )}
        />
        <span className="truncate text-sm font-semibold">{skill.name || skill.skill_id}</span>
        {skill.enabled && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            on
          </span>
        )}
        <Switch
          checked={skill.enabled}
          disabled={skill.scope === 'workspace' || toggling}
          onCheckedChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          className="ml-auto shrink-0 scale-75"
        />
      </div>
      {skill.description && (
        <p className="line-clamp-2 text-xs text-muted-foreground">{skill.description}</p>
      )}
      <div className="flex items-center gap-1 pt-0.5">
        <span className="font-mono text-[10px] text-muted-foreground/80">
          v{skill.installed_version}
        </span>
        <Badge variant="outline" className="px-1.5 text-[10px]">
          {skill.scope === 'workspace' ? 'workspace' : 'org'}
        </Badge>
      </div>
    </button>
  )
}

export function SkillsPanel({ wsId }: SkillsPanelProps) {
  const { skills, loading, loadAll, toggleSkill } = useWorkspaceSettingsStore()
  const [selected, setSelected] = useState<SkillInstall | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!skills) loadAll(client())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  const orgSkills = skills?.org_skills ?? []
  const workspaceSkills = skills?.workspace_skills ?? []
  const allSkills = [...orgSkills, ...workspaceSkills]
  const enabledCount = allSkills.filter((s) => s.enabled).length

  async function handleToggle(skill: SkillInstall, enabled: boolean): Promise<void> {
    if (skill.scope === 'workspace') return
    setToggling(skill.install_id)
    try {
      await toggleSkill(client(), skill.install_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-2 border-b border-border/70 px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Skills</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {enabledCount} of {allSkills.length} enabled in this workspace
          </p>
        </div>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="size-3.5" />
          Add skill
        </Button>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="skills-list"
          className="w-[320px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading && !skills ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">Loading…</p>
          ) : allSkills.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
              <p className="text-sm text-muted-foreground">No skills yet</p>
              <p className="text-xs text-muted-foreground/70">
                Click &ldquo;Add skill&rdquo; to install one.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3 p-3">
              {orgSkills.length > 0 && (
                <section className="flex flex-col gap-1.5">
                  <p className="px-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                    Org-installed
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {orgSkills.map((skill) => (
                      <li key={skill.install_id}>
                        <SkillItemCard
                          skill={skill}
                          active={selected?.install_id === skill.install_id}
                          toggling={toggling === skill.install_id}
                          onClick={() => setSelected(skill)}
                          onToggle={(v) => void handleToggle(skill, v)}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {workspaceSkills.length > 0 && (
                <section className="flex flex-col gap-1.5">
                  <p className="px-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
                    Workspace-private
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {workspaceSkills.map((skill) => (
                      <li key={skill.install_id}>
                        <SkillItemCard
                          skill={skill}
                          active={selected?.install_id === skill.install_id}
                          toggling={toggling === skill.install_id}
                          onClick={() => setSelected(skill)}
                          onToggle={(v) => void handleToggle(skill, v)}
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selected ? (
            <div className="flex w-full flex-col gap-4 p-6">
              <header className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-xl font-semibold tracking-tight">
                    {selected.name || selected.skill_id}
                  </h3>
                  <Badge variant="outline" className="font-mono">
                    v{selected.installed_version}
                  </Badge>
                  <Badge variant={selected.scope === 'workspace' ? 'secondary' : 'default'}>
                    {selected.scope === 'workspace' ? 'workspace-private' : 'org-installed'}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={cn(
                      selected.enabled
                        ? 'border-emerald-500/40 text-emerald-600'
                        : 'text-muted-foreground',
                    )}
                  >
                    {selected.enabled ? 'enabled' : 'disabled'}
                  </Badge>
                </div>
                {selected.description && (
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {selected.description}
                  </p>
                )}
              </header>

              <div className="rounded-lg border border-border/70 bg-card/40 p-4">
                <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-sm">
                  <dt className="text-muted-foreground">Skill ID</dt>
                  <dd className="font-mono text-xs">{selected.skill_id}</dd>
                  <dt className="text-muted-foreground">Install ID</dt>
                  <dd className="font-mono text-xs">{selected.install_id}</dd>
                  <dt className="text-muted-foreground">Scope</dt>
                  <dd>{selected.scope}</dd>
                  <dt className="text-muted-foreground">Version</dt>
                  <dd className="font-mono">{selected.installed_version}</dd>
                </dl>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
              Select a skill to view details
            </div>
          )}
        </section>
      </div>

      <AddSkillModal
        open={addOpen}
        onOpenChange={setAddOpen}
        client={client()}
        installedSkills={skills}
        onInstalled={() => void loadAll(client())}
      />
    </div>
  )
}
