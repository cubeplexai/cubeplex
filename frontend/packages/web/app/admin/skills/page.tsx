'use client'

import { useEffect, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useAdminSkillsStore, type SkillFilters } from '@cubebox/core'
import { SkillsToolbar } from '@/components/admin/skills/SkillsToolbar'
import { SkillsList } from '@/components/admin/skills/SkillsList'
import { SkillDetailPanel } from '@/components/admin/skills/SkillDetailPanel'
import { AdminCandidateDetailPanel } from '@/components/admin/skills/AdminCandidateDetailPanel'
import { UploadSkillModal } from '@/components/admin/skills/UploadSkillModal'
import { useAdminSkills } from '@/hooks/useAdminSkills'
import { PageHeader } from '@/components/management/PageHeader'

type Selection = { kind: 'skill'; id: string } | { kind: 'candidate'; candidateId: string }

export default function SkillsPage() {
  const t = useTranslations('admin')
  const [filters, setFilters] = useState<SkillFilters>({})
  const [selection, setSelection] = useState<Selection | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)

  const externalOnly = filters.externalOnly ?? false
  const { skills, loading, error, refresh } = useAdminSkills(externalOnly ? {} : filters)

  const search = useAdminSkillsStore((s) => s.search)
  const candidates = useAdminSkillsStore((s) => s.candidates)
  const searching = useAdminSkillsStore((s) => s.searching)
  const lastInstalled = useAdminSkillsStore((s) => s.lastInstalled)

  useEffect(() => {
    document.title = 'Skills'
  }, [])

  useEffect(() => {
    if (lastInstalled) void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastInstalled])

  const selectedCandidate =
    selection?.kind === 'candidate'
      ? (candidates.find((c) => c.candidate_id === selection.candidateId) ?? null)
      : null

  return (
    <div className="flex h-full flex-col">
      <PageHeader title={t('skills')} description={t('skillsSubtitle')} />

      <SkillsToolbar
        filters={filters}
        onFiltersChange={(next) => {
          setFilters(next)
          setSelection(null)
        }}
        onUploadClick={() => setUploadOpen(true)}
        onExternalSearch={(q) => void search(q)}
      />

      <div className="flex flex-1 overflow-hidden">
        <aside
          aria-label="skills-list"
          className="w-[360px] shrink-0 overflow-y-auto border-r border-border/70 bg-card/20"
        >
          <SkillsList
            skills={skills}
            loading={loading}
            error={error}
            selectedId={selection?.kind === 'skill' ? selection.id : null}
            onSelect={(id) => setSelection({ kind: 'skill', id })}
            candidates={candidates}
            searching={searching}
            externalOnly={externalOnly}
            selectedCandidateId={selection?.kind === 'candidate' ? selection.candidateId : null}
            onSelectCandidate={(id) => setSelection({ kind: 'candidate', candidateId: id })}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          {selectedCandidate ? (
            <AdminCandidateDetailPanel
              candidate={selectedCandidate}
              onInstalled={() => void refresh()}
            />
          ) : (
            <SkillDetailPanel
              skillId={selection?.kind === 'skill' ? selection.id : null}
              onActionDone={() => void refresh()}
            />
          )}
        </section>
      </div>

      <UploadSkillModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => void refresh()}
      />
    </div>
  )
}
