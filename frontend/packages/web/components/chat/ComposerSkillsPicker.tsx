'use client'

import { useEffect, useId, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useRouter } from 'next/navigation'
import useSWR from 'swr'
import { Check, ExternalLink, Loader2, Sparkles } from 'lucide-react'
import { formatSkillLabel, type SkillSummary } from '@cubeplex/core'
import { cn } from '@/lib/utils'
import type { ComposerSkillChip } from '@/lib/composer/skillChips'
import { ComposerOverlayShell } from './ComposerOverlayShell'

async function fetchSkills(url: string): Promise<SkillSummary[]> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skills fetch failed: ${res.status}`)
  return res.json() as Promise<SkillSummary[]>
}

export type ComposerSkillsPickerProps = {
  open: boolean
  workspaceId: string
  selectedIds: ReadonlySet<string>
  onToggle: (skill: ComposerSkillChip) => void
  onClose: () => void
}

export function ComposerSkillsPicker({
  open,
  workspaceId,
  selectedIds,
  onToggle,
  onClose,
}: ComposerSkillsPickerProps): React.ReactElement | null {
  const t = useTranslations('composerExtras')
  const router = useRouter()
  const listId = useId()
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)

  const { data, error, isLoading } = useSWR<SkillSummary[]>(
    open ? `/api/v1/ws/${workspaceId}/skills` : null,
    fetchSkills,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const skills = data ?? []
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return skills
    return skills.filter((s) => {
      if (s.name.toLowerCase().includes(q)) return true
      if (s.description.toLowerCase().includes(q)) return true
      if (s.keywords.some((k) => k.toLowerCase().includes(q))) return true
      return false
    })
  }, [skills, query])

  useEffect(() => {
    if (!open) {
      setQuery('')
      setActiveIndex(0)
    }
  }, [open])

  useEffect(() => {
    if (activeIndex >= filtered.length) {
      setActiveIndex(Math.max(0, filtered.length - 1))
    }
  }, [filtered.length, activeIndex])

  if (!open) return null

  const openManage = (): void => {
    onClose()
    router.push(`/w/${workspaceId}/skills`)
  }

  return (
    <ComposerOverlayShell
      role="dialog"
      aria-label={t('skillsPickerAria')}
      data-testid="composer-skills-picker"
    >
      <div className="shrink-0 border-b border-border px-2 py-1.5">
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActiveIndex(0)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              onClose()
              return
            }
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              if (filtered.length === 0) return
              setActiveIndex((i) => (i + 1) % filtered.length)
              return
            }
            if (e.key === 'ArrowUp') {
              e.preventDefault()
              if (filtered.length === 0) return
              setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              const skill = filtered[activeIndex]
              if (skill) {
                onToggle({ id: skill.id, name: skill.name })
              }
            }
          }}
          placeholder={t('skillsSearchPlaceholder')}
          aria-controls={listId}
          data-testid="composer-skills-search"
          className="h-8 w-full rounded-md bg-transparent px-2 text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <div id={listId} role="listbox" className="min-h-0 flex-1 overflow-y-auto p-1">
        {isLoading ? (
          <div className="flex h-16 items-center justify-center text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span className="sr-only">{t('loading')}</span>
          </div>
        ) : error ? (
          <div className="px-2 py-3 text-xs text-destructive">{t('skillsLoadError')}</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-3 text-xs text-muted-foreground">
            {skills.length === 0 ? t('skillsEmpty') : t('skillsNoMatches')}
          </div>
        ) : (
          filtered.map((skill, i) => {
            const label = formatSkillLabel(skill.name)
            const selected = selectedIds.has(skill.id)
            const active = i === activeIndex
            return (
              <button
                key={skill.id}
                type="button"
                role="option"
                aria-selected={selected}
                data-testid={`composer-skill-option-${skill.name}`}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => onToggle({ id: skill.id, name: skill.name })}
                className={cn(
                  'flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors',
                  active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60',
                )}
              >
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center text-muted-foreground">
                  <Sparkles className="size-3.5" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{label.primary}</span>
                  {skill.description ? (
                    <span className="mt-0.5 block line-clamp-2 text-[11px] text-muted-foreground">
                      {skill.description}
                    </span>
                  ) : null}
                </span>
                {selected ? (
                  <Check className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
                ) : null}
              </button>
            )
          })
        )}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border px-2 py-1.5">
        <span className="text-[11px] text-muted-foreground">{t('skillsFooterHint')}</span>
        <button
          type="button"
          onClick={openManage}
          data-testid="composer-skills-manage"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          {t('manageSkills')}
          <ExternalLink className="size-3" aria-hidden />
        </button>
      </div>
    </ComposerOverlayShell>
  )
}
