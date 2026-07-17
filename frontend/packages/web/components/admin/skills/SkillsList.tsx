'use client'

import { useTranslations } from 'next-intl'
import type { SkillCandidateOut, SkillSummary } from '@cubeplex/core'
import { CandidateCard } from '@/components/skills/CandidateCard'
import { SkillCard } from './SkillCard'

interface SkillsListProps {
  skills: SkillSummary[]
  loading: boolean
  error: Error | undefined
  selectedId: string | null
  onSelect: (id: string) => void
  // External registry
  candidates: SkillCandidateOut[]
  searching: boolean
  externalOnly: boolean
  selectedCandidateId: string | null
  onSelectCandidate: (id: string) => void
}

export function SkillsList({
  skills,
  loading,
  error,
  selectedId,
  onSelect,
  candidates,
  searching,
  externalOnly,
  selectedCandidateId,
  onSelectCandidate,
}: SkillsListProps) {
  const t = useTranslations('adminSkills')

  if (externalOnly) {
    if (searching) {
      return (
        <div className="flex flex-col gap-1.5 p-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-[76px] animate-pulse rounded-lg border border-border/50 bg-muted/30"
            />
          ))}
        </div>
      )
    }
    if (candidates.length === 0) {
      return (
        <div className="flex flex-col items-center justify-center gap-1 px-6 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t('noExternalResults')}</p>
          <p className="text-xs text-muted-foreground/70">{t('noExternalResultsHint')}</p>
        </div>
      )
    }
    return (
      <ul className="flex flex-col gap-1.5 p-3">
        {candidates.map((c) => (
          <li key={c.candidate_id}>
            <CandidateCard
              candidate={c}
              active={c.candidate_id === selectedCandidateId}
              onClick={() => onSelectCandidate(c.candidate_id)}
            />
          </li>
        ))}
      </ul>
    )
  }

  // Normal catalog view
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
        {t('loading')}
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
        {t('loadFailed', { message: error.message })}
      </div>
    )
  }

  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-2 px-4 pb-1 pt-2">
        <span className="text-xs font-semibold text-muted-foreground">{t('systemCatalog')}</span>
        <div className="flex-1 border-t border-border/50" />
      </div>

      {skills.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-1 px-6 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t('noSkills')}</p>
          <p className="text-xs text-muted-foreground/70">{t('noSkillsHint')}</p>
        </div>
      ) : (
        <ul data-testid="skills-list" className="flex flex-col gap-1.5 p-3">
          {skills.map((skill) => (
            <li key={skill.id}>
              <SkillCard
                skill={skill}
                active={skill.id === selectedId}
                onClick={() => onSelect(skill.id)}
              />
            </li>
          ))}
        </ul>
      )}

      {(candidates.length > 0 || searching) && (
        <>
          <div className="flex items-center gap-2 px-4 py-2">
            <span className="text-xs font-semibold text-muted-foreground">
              {t('externalSources')}
            </span>
            {searching && (
              <div className="flex gap-1">
                <div className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                <div className="animation-delay-200 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
                <div className="animation-delay-400 h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground" />
              </div>
            )}
            <div className="flex-1 border-t border-border/50" />
          </div>
          {searching && candidates.length === 0 ? (
            <div className="flex flex-col gap-1.5 px-3 pb-3">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-[76px] animate-pulse rounded-lg border border-border/50 bg-muted/30"
                />
              ))}
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5 px-3 pb-3">
              {candidates.map((c) => (
                <li key={c.candidate_id}>
                  <CandidateCard
                    candidate={c}
                    active={c.candidate_id === selectedCandidateId}
                    onClick={() => onSelectCandidate(c.candidate_id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
