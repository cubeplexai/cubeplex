'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Download, ExternalLink } from 'lucide-react'
import { usePanelStore } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { csrfHeaders } from '@/lib/csrf'
import { cn } from '@/lib/utils'

export interface SkillCandidate {
  candidate_id: string
  name: string
  canonical_name: string
  description: string
  source: string
  source_name: string
  repo: string | null
  trust: 'official' | 'community' | 'untrusted'
  install_state: 'enabled' | 'in_catalog' | 'available'
  install_count: number | null
  unvetted: boolean
}

const TRUST_BADGE: Record<string, string> = {
  official: 'bg-info-surface text-info-fg',
  community: 'bg-warning-surface text-warning-fg',
  untrusted: 'bg-muted text-muted-foreground',
}

const STATE_BADGE: Record<string, string> = {
  enabled: 'bg-success-surface text-success-fg',
  in_catalog: 'bg-muted text-muted-foreground',
  available: 'bg-muted text-muted-foreground',
}

function safeRepoUrl(url: string | null): string | null {
  if (!url) return null
  try {
    return new URL(url).protocol === 'https:' ? url : null
  } catch {
    return null
  }
}

export function SkillCandidateCard({ candidate }: { candidate: SkillCandidate }) {
  const t = useTranslations('skillSearch')
  const { workspaceId } = useWorkspaceContext()
  const openSkillCandidate = usePanelStore((s) => s.openSkillCandidate)

  const [installing, setInstalling] = useState(false)
  const [installState, setInstallState] = useState<SkillCandidate['install_state']>(
    candidate.install_state,
  )
  const [installError, setInstallError] = useState<string | null>(null)

  async function handleInstall(): Promise<void> {
    if (!workspaceId || installState === 'enabled') return
    setInstalling(true)
    setInstallError(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/install`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: candidate.candidate_id }),
      })
      if (!res.ok) {
        setInstallError(t('installError'))
        return
      }
      setInstallState('enabled')
    } finally {
      setInstalling(false)
    }
  }

  const TRUST_KEY = {
    official: 'trustOfficial',
    community: 'trustCommunity',
    untrusted: 'trustUnvetted',
  } as const satisfies Record<
    SkillCandidate['trust'],
    'trustOfficial' | 'trustCommunity' | 'trustUnvetted'
  >
  const trustLabel = t(TRUST_KEY[candidate.trust] ?? 'trustUnvetted')
  const stateLabel = installState === 'enabled' ? t('stateEnabled') : t('stateAvailable')

  return (
    <div className="rounded-xl border border-border bg-card p-3 flex flex-col gap-2 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono text-sm font-semibold truncate">{candidate.name}</span>
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                TRUST_BADGE[candidate.trust] ?? TRUST_BADGE.untrusted,
              )}
            >
              {trustLabel}
            </span>
            <span
              className={cn(
                'rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                STATE_BADGE[installState] ?? STATE_BADGE.available,
              )}
            >
              {stateLabel}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">{candidate.source_name}</span>
            {safeRepoUrl(candidate.repo) && (
              <a
                href={safeRepoUrl(candidate.repo)!}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
                onClick={(e) => e.stopPropagation()}
              >
                <span>{candidate.repo!.replace('https://github.com/', '')}</span>
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
        </div>
        {candidate.install_count != null && (
          <div className="flex items-center gap-0.5 shrink-0 text-xs text-muted-foreground">
            <Download className="size-3" />
            <span>{candidate.install_count.toLocaleString()}</span>
          </div>
        )}
      </div>

      <p className="text-xs text-foreground/80 leading-relaxed">
        {candidate.description || t('noDescription')}
      </p>

      {installError && <p className="text-xs text-destructive">{installError}</p>}

      <div className="flex gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() =>
            openSkillCandidate(
              candidate.candidate_id,
              safeRepoUrl(candidate.repo),
              candidate.source_name,
            )
          }
        >
          {t('preview')}
        </Button>
        <Button
          size="sm"
          className="h-7 text-xs"
          disabled={installing || installState === 'enabled'}
          onClick={() => void handleInstall()}
        >
          {installState === 'enabled'
            ? t('installed')
            : installing
              ? t('installing')
              : t('install')}
        </Button>
      </div>
    </div>
  )
}
