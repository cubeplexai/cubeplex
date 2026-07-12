'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Download, ExternalLink, Sparkles } from 'lucide-react'
import useSWR from 'swr'
import { usePanelStore } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { PanelHeader } from '@/components/panel/PanelHeader'
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
  if (!res.ok) {
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: string }
      detail = typeof body.detail === 'string' ? body.detail : ''
    } catch {
      /* ignore parse failures */
    }
    throw new Error(detail || `${res.status}`)
  }
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
  const close = usePanelStore((s) => s.close)

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
    <div className="flex h-full flex-col bg-background">
      <PanelHeader
        source={{
          kind: 'plain',
          icon: <Sparkles className="size-3.5 text-primary shrink-0" />,
          title: data?.name ?? sourceName ?? t('loading'),
          subtitle: sourceName && data ? sourceName : undefined,
        }}
        onClose={close}
      />
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
        {isLoading && <p className="text-sm text-muted-foreground">{t('loading')}</p>}

        {error && !isLoading && (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-destructive">{t('fetchError')}</p>
            {error instanceof Error && error.message && (
              <p className="font-mono text-xs text-muted-foreground">{error.message}</p>
            )}
            <Button variant="outline" size="sm" onClick={() => void mutate()}>
              {t('retry')}
            </Button>
          </div>
        )}

        {data && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {safeRepoUrl(repo) && (
                <a
                  href={safeRepoUrl(repo)!}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground transition-colors duration-fast"
                >
                  <span>{repo!.replace('https://github.com/', '')}</span>
                  <ExternalLink className="size-3" />
                </a>
              )}
              {data.env_vars.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  requires: {data.env_vars.join(', ')}
                </span>
              )}
            </div>

            {installError && (
              <p className="rounded bg-destructive/10 px-3 py-2 text-sm text-destructive">
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
