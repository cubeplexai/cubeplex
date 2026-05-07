'use client'

import { useEffect, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { Package, Sparkles, X } from 'lucide-react'
import {
  installWorkspaceSkill,
  listSkillCatalog,
  type ApiClient,
  type SkillCatalogEntry,
  type WorkspaceSkills,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface AddSkillModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  client: ApiClient
  installedSkills: WorkspaceSkills | null
  onInstalled: () => void
}

export function AddSkillModal({
  open,
  onOpenChange,
  client,
  installedSkills,
  onInstalled,
}: AddSkillModalProps) {
  const [catalog, setCatalog] = useState<SkillCatalogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!open) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setError(null)
    setQuery('')
    listSkillCatalog(client)
      .then(setCatalog)
      .catch((err) => setError(String(err)))
  }, [open, client])

  const installedIds = new Set([
    ...(installedSkills?.org_skills.map((s) => s.skill_id) ?? []),
    ...(installedSkills?.workspace_skills.map((s) => s.skill_id) ?? []),
  ])

  const filtered = (catalog ?? []).filter((entry) => {
    if (installedIds.has(entry.id)) return false
    if (!query) return true
    const q = query.toLowerCase()
    return entry.name.toLowerCase().includes(q) || entry.description.toLowerCase().includes(q)
  })

  async function handleInstall(entry: SkillCatalogEntry): Promise<void> {
    setInstalling(entry.id)
    setError(null)
    try {
      await installWorkspaceSkill(client, entry.id, entry.current_version)
      onInstalled()
      onOpenChange(false)
    } catch (err) {
      setError(String(err))
    } finally {
      setInstalling(null)
    }
  }

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <DialogPrimitive.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(560px,calc(100vw-32px))] max-h-[80vh] -translate-x-1/2 -translate-y-1/2',
            'flex flex-col rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl',
            'transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0',
          )}
        >
          <header className="flex items-start justify-between gap-3 border-b border-border/60 px-5 py-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">
                Install skill into this workspace
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="mt-0.5 text-xs text-muted-foreground">
                Pick from skills visible to your organization. Already-installed skills are hidden.
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              render={
                <button
                  type="button"
                  aria-label="close"
                  className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              }
            />
          </header>

          <div className="px-5 pt-3">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or description"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-3">
            {error && (
              <div className="mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {error}
              </div>
            )}
            {!catalog ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Loading…</p>
            ) : filtered.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {query ? 'No matches' : 'No more skills available'}
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {filtered.map((entry) => {
                  const SourceIcon = entry.source === 'preinstalled' ? Sparkles : Package
                  return (
                    <li key={entry.id}>
                      <button
                        type="button"
                        onClick={() => void handleInstall(entry)}
                        disabled={installing === entry.id}
                        className={cn(
                          'group flex w-full flex-col gap-1.5 rounded-lg border border-border/70 bg-card/40 p-3 text-left transition-all',
                          'hover:border-border hover:bg-accent/40 disabled:opacity-50',
                        )}
                      >
                        <div className="flex items-center gap-2">
                          <SourceIcon
                            className={cn(
                              'size-3.5 shrink-0',
                              entry.source === 'preinstalled'
                                ? 'text-primary'
                                : 'text-muted-foreground',
                            )}
                          />
                          <span className="truncate text-sm font-semibold">{entry.name}</span>
                          <span className="ml-auto font-mono text-[10px] text-muted-foreground/80">
                            v{entry.current_version}
                          </span>
                        </div>
                        {entry.description && (
                          <p className="line-clamp-2 text-xs text-muted-foreground">
                            {entry.description}
                          </p>
                        )}
                        {entry.keywords.length > 0 && (
                          <div className="flex flex-wrap gap-1 pt-0.5">
                            {entry.keywords.slice(0, 3).map((kw) => (
                              <Badge key={kw} variant="outline" className="px-1.5 text-[10px]">
                                {kw}
                              </Badge>
                            ))}
                          </div>
                        )}
                        {installing === entry.id && (
                          <p className="text-[11px] text-primary">Installing…</p>
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
