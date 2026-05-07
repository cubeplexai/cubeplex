'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import {
  useWorkspaceSkillsCatalog,
  type WorkspaceSkillEntry,
  type WorkspaceSkillFilters,
} from '@/hooks/useWorkspaceSkillsCatalog'
import { UploadWorkspaceSkillModal } from './skills/UploadWorkspaceSkillModal'
import { WorkspaceSkillCard } from './skills/WorkspaceSkillCard'
import { WorkspaceSkillDetail } from './skills/WorkspaceSkillDetail'
import { WorkspaceSkillsToolbar } from './skills/WorkspaceSkillsToolbar'

interface SkillsPanelProps {
  wsId: string
}

export function SkillsPanel({ wsId }: SkillsPanelProps) {
  const t = useTranslations('wsSettings.skillsList')
  const [filters, setFilters] = useState<WorkspaceSkillFilters>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const { skills, loading, error, refresh } = useWorkspaceSkillsCatalog(wsId, filters)

  const selected = skills.find((s) => s.id === selectedId) ?? null

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('title')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('subtitle')}</p>
      </header>

      <WorkspaceSkillsToolbar
        filters={filters}
        onFiltersChange={setFilters}
        onAddClick={() => setUploadOpen(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label={t('listAria')}
          className="w-[360px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          {loading && skills.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
          ) : error ? (
            <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
              {error.message}
            </div>
          ) : skills.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-1 px-6 text-center">
              <p className="text-sm text-muted-foreground">{t('noMatch')}</p>
              <p className="text-xs text-muted-foreground/70">{t('noMatchHint')}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 p-3">
              {skills.map((s: WorkspaceSkillEntry) => (
                <li key={s.id}>
                  <WorkspaceSkillCard
                    skill={s}
                    active={s.id === selectedId}
                    onClick={() => setSelectedId(s.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selected ? (
            <WorkspaceSkillDetail
              wsId={wsId}
              skill={selected}
              onActionDone={() => void refresh()}
            />
          ) : (
            <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted-foreground">
              {t('selectSkill')}
            </div>
          )}
        </section>
      </div>

      <UploadWorkspaceSkillModal
        wsId={wsId}
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => void refresh()}
      />
    </div>
  )
}
