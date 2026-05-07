'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  createApiClient,
  installWorkspaceSkill,
  listSkillCatalog,
  useWorkspaceSettingsStore,
} from '@cubebox/core'
import type { SkillCatalogEntry, SkillInstall } from '@cubebox/core'
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
  const [showAddForm, setShowAddForm] = useState(false)
  const [catalog, setCatalog] = useState<SkillCatalogEntry[] | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)

  const client = useCallback(() => {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }, [wsId])

  useEffect(() => {
    if (!skills) loadAll(client())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId])

  const openAddForm = async () => {
    setShowAddForm(true)
    if (catalog) return
    try {
      const entries = await listSkillCatalog(client())
      setCatalog(entries)
    } catch (err) {
      setCatalogError(String(err))
    }
  }

  const handleInstall = async (entry: SkillCatalogEntry) => {
    setInstalling(entry.id)
    try {
      await installWorkspaceSkill(client(), entry.id, entry.current_version)
      await loadAll(client())
      setShowAddForm(false)
    } catch (err) {
      setCatalogError(String(err))
    } finally {
      setInstalling(null)
    }
  }

  const orgSkills = skills?.org_skills ?? []
  const workspaceSkills = skills?.workspace_skills ?? []
  const allSkills = [...orgSkills, ...workspaceSkills]

  const handleToggle = async (skill: SkillInstall, enabled: boolean) => {
    if (skill.scope === 'workspace') return
    setToggling(skill.install_id)
    try {
      await toggleSkill(client(), skill.install_id, enabled)
    } finally {
      setToggling(null)
    }
  }

  const renderSkillItem = (skill: SkillInstall) => (
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
          <p className="text-[12px] font-medium truncate">{skill.name || skill.skill_id}</p>
          <p className="text-[10px] text-muted-foreground/60 truncate">
            {skill.description || skill.installed_version}
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
  )

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Col 2: list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="p-3 border-b border-border flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-semibold">Skills</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {allSkills.filter((s) => s.enabled).length} / {allSkills.length} enabled
            </p>
          </div>
          <button
            onClick={() => (showAddForm ? setShowAddForm(false) : openAddForm())}
            className="text-[11px] text-muted-foreground hover:text-foreground mt-0.5 shrink-0"
          >
            {showAddForm ? 'Cancel' : '+ Add'}
          </button>
        </div>
        {showAddForm && (
          <div className="p-2 border-b border-border max-h-64 overflow-y-auto">
            {catalogError ? (
              <p className="text-xs text-destructive px-2 py-2">Failed to load: {catalogError}</p>
            ) : !catalog ? (
              <p className="text-xs text-muted-foreground px-2 py-2">Loading catalog…</p>
            ) : catalog.length === 0 ? (
              <p className="text-xs text-muted-foreground px-2 py-2">No skills available</p>
            ) : (
              <ul className="space-y-1">
                {catalog
                  .filter(
                    (entry) =>
                      // Hide ones already installed in this workspace (org or private)
                      !skills?.org_skills.some((s) => s.skill_id === entry.id) &&
                      !skills?.workspace_skills.some((s) => s.skill_id === entry.id),
                  )
                  .map((entry) => (
                    <li key={entry.id}>
                      <button
                        onClick={() => handleInstall(entry)}
                        disabled={installing === entry.id}
                        className="w-full text-left p-2 rounded-md hover:bg-accent/60 disabled:opacity-50"
                      >
                        <p className="text-[12px] font-medium truncate">{entry.name}</p>
                        <p className="text-[10px] text-muted-foreground/70 truncate">
                          {entry.description || `${entry.source} · ${entry.current_version}`}
                        </p>
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </div>
        )}
        <div className="p-2">
          {loading && !skills ? (
            <p className="text-xs text-muted-foreground px-2 py-4">Loading…</p>
          ) : allSkills.length === 0 ? (
            <p className="text-xs text-muted-foreground px-2 py-4">No skills available</p>
          ) : (
            <>
              {orgSkills.length > 0 && (
                <div className="mb-2">
                  <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-1">
                    Org-installed
                  </p>
                  <ul className="space-y-0.5">{orgSkills.map(renderSkillItem)}</ul>
                </div>
              )}
              {workspaceSkills.length > 0 && (
                <div className="mb-2">
                  <p className="px-2 text-[9px] font-semibold uppercase tracking-widest text-muted-foreground/50 mb-1">
                    Workspace private
                  </p>
                  <ul className="space-y-0.5">{workspaceSkills.map(renderSkillItem)}</ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Col 3: detail */}
      <div className="flex-1 overflow-y-auto p-8">
        {selected ? (
          <>
            <h2 className="text-base font-semibold mb-1">{selected.name || selected.skill_id}</h2>
            {selected.description && (
              <p className="text-sm text-muted-foreground mb-4">{selected.description}</p>
            )}
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
                <span>Skill ID</span>
                <span className="font-mono text-xs">{selected.skill_id}</span>
              </div>
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
