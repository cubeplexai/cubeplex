'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Download, ExternalLink } from 'lucide-react'
import useSWR from 'swr'
import { Button } from '@/components/ui/button'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { csrfHeaders } from '@/lib/csrf'
import { proseClasses } from '@/lib/utils'

interface CandidatePreview {
  candidate_id: string
  name: string
  canonical_name: string
  content: string
  env_vars: string[]
}

async function fetchPreview(url: string): Promise<CandidatePreview> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`preview fetch failed: ${res.status}`)
  return res.json() as Promise<CandidatePreview>
}

function safeRepoUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    return new URL(url).protocol === 'https:' ? url : null
  } catch {
    return null
  }
}

export function SkillCandidatePanel({
  candidateId,
  repo,
  sourceName,
}: {
  candidateId: string
  repo?: string | null
  sourceName?: string
}) {
  const t = useTranslations('panel.skillCandidatePanel')
  const { workspaceId } = useWorkspaceContext()

  const url = workspaceId
    ? `/api/v1/ws/${workspaceId}/skills/discover/preview?candidate_id=${encodeURIComponent(candidateId)}`
    : null

  const { data, error, isLoading, mutate } = useSWR<CandidatePreview>(url, fetchPreview, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const [installing, setInstalling] = useState(false)
  const [installState, setInstallState] = useState<'idle' | 'done' | 'error'>('idle')
  const [installError, setInstallError] = useState<string | null>(null)

  async function handleInstall(): Promise<void> {
    if (!workspaceId) return
    setInstalling(true)
    setInstallError(null)
    try {
      const res = await fetch(`/api/v1/ws/${workspaceId}/skills/install`, {
        method: 'POST',
        credentials: 'include',
        headers: { ...csrfHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: candidateId }),
      })
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>
        setInstallError(typeof body.detail === 'string' ? body.detail : t('installError'))
        setInstallState('error')
        return
      }
      setInstallState('done')
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        {isLoading && <p className="text-sm text-muted-foreground">{t('loading')}</p>}

        {error && !isLoading && (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-destructive">{t('fetchError')}</p>
            <Button variant="outline" size="sm" onClick={() => void mutate()}>
              {t('retry')}
            </Button>
          </div>
        )}

        {data && (
          <>
            <header className="flex flex-col gap-1">
              <span className="font-mono font-semibold">{data.name}</span>
              <div className="flex flex-wrap items-center gap-2">
                {sourceName && <span className="text-xs text-muted-foreground">{sourceName}</span>}
                {safeRepoUrl(repo) && (
                  <a
                    href={safeRepoUrl(repo)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
                  >
                    <ExternalLink className="size-3" />
                    <span>{repo!.replace('https://github.com/', '')}</span>
                  </a>
                )}
                {data.env_vars.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    requires: {data.env_vars.join(', ')}
                  </span>
                )}
              </div>
            </header>

            {installError && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {installError}
              </p>
            )}

            <div className={proseClasses}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown>
            </div>
          </>
        )}
      </div>

      {data && (
        <div className="shrink-0 border-t p-4">
          <Button
            size="sm"
            disabled={installing || installState === 'done'}
            onClick={() => void handleInstall()}
            className="flex items-center gap-1.5"
          >
            <Download className="size-3.5" />
            {installState === 'done'
              ? t('installed')
              : installing
                ? t('installing')
                : t('installButton')}
          </Button>
        </div>
      )}
    </div>
  )
}
