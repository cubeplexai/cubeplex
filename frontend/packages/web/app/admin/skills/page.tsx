'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import type { SkillFilters } from '@cubebox/core'
import { SkillsToolbar } from '@/components/admin/skills/SkillsToolbar'
import { SkillsList } from '@/components/admin/skills/SkillsList'
import { SkillDetailPanel } from '@/components/admin/skills/SkillDetailPanel'
import { UploadSkillModal } from '@/components/admin/skills/UploadSkillModal'
import { useAdminSkills } from '@/hooks/useAdminSkills'

export default function SkillsPage() {
  const t = useTranslations('admin')
  const [filters, setFilters] = useState<SkillFilters>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [uploadOpen, setUploadOpen] = useState(false)
  const { skills, loading, error, refresh } = useAdminSkills(filters)

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-border/70 px-6 py-4">
        <h2 className="text-lg font-semibold tracking-tight">{t('skills')}</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{t('skillsSubtitle')}</p>
      </header>

      <SkillsToolbar
        filters={filters}
        onFiltersChange={setFilters}
        onUploadClick={() => setUploadOpen(true)}
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
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </aside>

        <section className="flex flex-1 overflow-y-auto">
          <SkillDetailPanel
            skillId={selectedId}
            onActionDone={() => {
              void refresh()
            }}
          />
        </section>
      </div>

      <UploadSkillModal
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={() => {
          void refresh()
        }}
      />
    </div>
  )
}
