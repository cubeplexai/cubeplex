'use client'

import { use, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { createApiClient, useSkillsStore } from '@cubeplex/core'
import { useWorkspaceSkillsCatalog } from '@/hooks/useWorkspaceSkillsCatalog'
import type { WorkspaceSkillEntry, WorkspaceSkillFilters } from '@/hooks/useWorkspaceSkillsCatalog'
import { WorkspaceSkillCard } from '@/components/workspace-settings/skills/WorkspaceSkillCard'
import { WorkspaceSkillDetail } from '@/components/workspace-settings/skills/WorkspaceSkillDetail'
import { WorkspaceSkillsToolbar } from '@/components/workspace-settings/skills/WorkspaceSkillsToolbar'
import { UploadWorkspaceSkillModal } from '@/components/workspace-settings/skills/UploadWorkspaceSkillModal'
import { CandidateCard } from '@/components/skills/CandidateCard'
import { CandidateDetailPanel } from '@/components/skills/CandidateDetailPanel'
import { PageHeader } from '@/components/management/PageHeader'
import { ListDetailLayout } from '@/components/shared/ListDetailLayout'

type Selection = { kind: 'skill'; id: string } | { kind: 'candidate'; candidateId: string }

export default function WorkspaceSkillsPage({ params }: { params: Promise<{ wsId: string }> }) {
  const { wsId } = use(params)
  const t = useTranslations('wsSettings.skillsList')
  const [filters, setFilters] = useState<WorkspaceSkillFilters>({})
  const [selection, setSelection] = useState<Selection | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const { skills, loading, error, refresh } = useWorkspaceSkillsCatalog(wsId, filters)
  const lastInstalled = useSkillsStore((s) => s.lastInstalled)
  const search = useSkillsStore((s) => s.search)
  const candidates = useSkillsStore((s) => s.candidates)
  const searching = useSkillsStore((s) => s.searching)
  // discoverSkills builds the full path with wsId — do not set workspaceId on this client
  const apiClient = useMemo(() => createApiClient(''), [])

  const selectedSkill =
    selection?.kind === 'skill' ? (skills.find((s) => s.id === selection.id) ?? null) : null
  const selectedCandidate =
    selection?.kind === 'candidate'
      ? (candidates.find((c) => c.candidate_id === selection.candidateId) ?? null)
      : null

  useEffect(() => {
    document.title = 'Skills'
  }, [])

  // Refresh catalog whenever a skill is installed via an external candidate
  useEffect(() => {
    if (lastInstalled) void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastInstalled])

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('title')} description={t('subtitle')} />

      <WorkspaceSkillsToolbar
        filters={filters}
        onFiltersChange={setFilters}
        onAddClick={() => setUploadOpen(true)}
        onSearch={(q) => void search(apiClient, wsId, q)}
      />

      <ListDetailLayout
        selected={selection !== null}
        onBack={() => setSelection(null)}
        backLabel={t('back')}
        placeholder={t('selectSkill')}
        railClassName="border-border bg-card/20 px-0 py-0"
        list={
          <div aria-label={t('listAria')} data-testid="skills-list">
            {filters.externalOnly ? (
              searching ? (
                <div className="flex flex-col gap-1.5 p-3">
                  {[1, 2, 3].map((i) => (
                    <div
                      key={i}
                      className="h-[76px] animate-pulse rounded-lg border border-border bg-accent"
                    />
                  ))}
                </div>
              ) : candidates.filter((c) => c.source_kind === 'remote').length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-1 px-6 py-8 text-center">
                  <p className="text-sm text-muted-foreground">{t('noMatch')}</p>
                  <p className="text-xs text-muted-foreground/70">{t('noMatchHint')}</p>
                </div>
              ) : (
                <ul className="flex flex-col gap-1.5 p-3">
                  {candidates
                    .filter((c) => c.source_kind === 'remote')
                    .map((c) => (
                      <li key={c.candidate_id}>
                        <CandidateCard
                          candidate={c}
                          active={
                            selection?.kind === 'candidate' &&
                            selection.candidateId === c.candidate_id
                          }
                          onClick={() =>
                            setSelection({ kind: 'candidate', candidateId: c.candidate_id })
                          }
                        />
                      </li>
                    ))}
                </ul>
              )
            ) : loading && skills.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t('loading')}</p>
            ) : error ? (
              <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                {error.message}
              </div>
            ) : (
              <div className="flex flex-col">
                <div className="flex items-center gap-2 px-4 pb-1 pt-2">
                  <span className="text-xs font-semibold text-muted-foreground">
                    {t('systemCatalog')}
                  </span>
                  <div className="flex-1 border-t border-border" />
                </div>

                {skills.length === 0 ? (
                  <div className="flex flex-col items-center justify-center gap-1 px-6 py-8 text-center">
                    <p className="text-sm text-muted-foreground">{t('noMatch')}</p>
                    <p className="text-xs text-muted-foreground/70">{t('noMatchHint')}</p>
                  </div>
                ) : (
                  <ul className="flex flex-col gap-1.5 p-3">
                    {skills.map((s: WorkspaceSkillEntry) => (
                      <li key={s.id}>
                        <WorkspaceSkillCard
                          skill={s}
                          active={selection?.kind === 'skill' && selection.id === s.id}
                          onClick={() => setSelection({ kind: 'skill', id: s.id })}
                        />
                      </li>
                    ))}
                  </ul>
                )}

                {(candidates.filter((c) => c.source_kind === 'remote').length > 0 || searching) && (
                  <>
                    <div className="flex items-center gap-2 px-4 py-2">
                      <span className="text-xs font-semibold text-muted-foreground">
                        {t('externalSources')}
                      </span>
                      {searching && (
                        <div className="ml-auto flex items-center gap-1.5">
                          <div className="flex gap-1">
                            <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                            <div className="animation-delay-200 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                            <div className="animation-delay-400 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                          </div>
                        </div>
                      )}
                      <div className="flex-1 border-t border-border" />
                    </div>
                    {searching &&
                    candidates.filter((c) => c.source_kind === 'remote').length === 0 ? (
                      <div className="flex flex-col gap-1.5 px-3 pb-3">
                        {[1, 2, 3].map((i) => (
                          <div
                            key={i}
                            className="h-[76px] animate-pulse rounded-lg border border-border bg-accent"
                          />
                        ))}
                      </div>
                    ) : (
                      <ul className="flex flex-col gap-1.5 px-3 pb-3">
                        {candidates
                          .filter((c) => c.source_kind === 'remote')
                          .map((c) => (
                            <li key={c.candidate_id}>
                              <CandidateCard
                                candidate={c}
                                active={
                                  selection?.kind === 'candidate' &&
                                  selection.candidateId === c.candidate_id
                                }
                                onClick={() =>
                                  setSelection({ kind: 'candidate', candidateId: c.candidate_id })
                                }
                              />
                            </li>
                          ))}
                      </ul>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        }
        detail={
          selectedSkill ? (
            <WorkspaceSkillDetail
              wsId={wsId}
              skill={selectedSkill}
              onActionDone={() => void refresh()}
            />
          ) : selectedCandidate ? (
            <CandidateDetailPanel wsId={wsId} candidate={selectedCandidate} />
          ) : null
        }
      />

      <UploadWorkspaceSkillModal
        wsId={wsId}
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => void refresh()}
      />
    </div>
  )
}
