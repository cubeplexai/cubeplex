'use client'

import { useMemo } from 'react'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Globe, ShieldCheck, ShieldAlert, ShieldOff } from 'lucide-react'
import { createApiClient, useSkillsStore, type SkillCandidateOut } from '@cubebox/core'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn, proseClasses } from '@/lib/utils'

function TrustInfo({ trust }: { trust: SkillCandidateOut['trust'] }) {
  if (trust === 'official') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
        <ShieldCheck className="size-3.5" />
        Official
      </span>
    )
  }
  if (trust === 'community') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-500/10 px-2 py-1 text-xs font-medium text-blue-600 dark:text-blue-400">
        <ShieldAlert className="size-3.5" />
        Community
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-600 dark:text-amber-400">
      <ShieldOff className="size-3.5" />
      Unvetted
    </span>
  )
}

interface CandidateDetailPanelProps {
  wsId: string
  candidate: SkillCandidateOut
}

async function previewFetcher(url: string): Promise<{ content: string }> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<{ content: string }>
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

export function CandidateDetailPanel({ wsId, candidate }: CandidateDetailPanelProps) {
  const install = useSkillsStore((s) => s.install)
  const installing = useSkillsStore((s) => s.installing[candidate.candidate_id] ?? false)
  const apiClient = useMemo(() => createApiClient(''), [])

  const isInstalled = candidate.install_state === 'enabled'

  const { data: preview, isLoading } = useSWR<{ content: string }>(
    `/api/v1/ws/${wsId}/skills/discover/preview?candidate_id=${candidate.candidate_id}`,
    previewFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Globe className="size-4 shrink-0 text-muted-foreground" />
            <h3 className="text-base font-semibold">{candidate.name}</h3>
          </div>
          <span className="font-mono text-xs text-muted-foreground">
            {candidate.canonical_name}
          </span>
        </div>
        <Button
          size="sm"
          disabled={installing || isInstalled}
          onClick={() => void install(apiClient, wsId, candidate.candidate_id)}
        >
          {isInstalled ? 'Installed' : installing ? 'Installing…' : 'Install'}
        </Button>
      </div>

      {candidate.description && (
        <p className="text-sm leading-relaxed text-muted-foreground">{candidate.description}</p>
      )}

      <dl className="flex flex-col gap-4 border-b border-border pb-4">
        <div className="flex items-center gap-3">
          <dt className="min-w-20 text-xs font-medium text-muted-foreground">Source</dt>
          <dd className="text-sm">{candidate.source_name}</dd>
        </div>
        <div className="flex items-center gap-3">
          <dt className="min-w-20 text-xs font-medium text-muted-foreground">Trust</dt>
          <dd>
            <TrustInfo trust={candidate.trust} />
          </dd>
        </div>
        {candidate.version && (
          <div className="flex items-center gap-3">
            <dt className="min-w-20 text-xs font-medium text-muted-foreground">Version</dt>
            <dd className="font-mono text-sm">{candidate.version}</dd>
          </div>
        )}
        {candidate.install_count !== null && (
          <div className="flex items-center gap-3">
            <dt className="min-w-20 text-xs font-medium text-muted-foreground">Installs</dt>
            <dd className="text-sm">{candidate.install_count.toLocaleString()}</dd>
          </div>
        )}
        {candidate.repo && (
          <div className="flex items-center gap-3">
            <dt className="min-w-20 text-xs font-medium text-muted-foreground">Repo</dt>
            <dd className="truncate text-xs text-muted-foreground">{candidate.repo}</dd>
          </div>
        )}
        {candidate.keywords.length > 0 && (
          <div className="flex items-start gap-3">
            <dt className="min-w-20 pt-0.5 text-xs font-medium text-muted-foreground">Keywords</dt>
            <dd className="flex flex-wrap gap-1">
              {candidate.keywords.map((kw) => (
                <Badge key={kw} variant="outline" className="text-xs">
                  {kw}
                </Badge>
              ))}
            </dd>
          </div>
        )}
      </dl>

      <div className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold text-muted-foreground">Overview</h3>
        {preview && (
          <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
            <div className={cn('space-y-4 text-sm', proseClasses)}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {stripFrontmatter(preview.content)}
              </ReactMarkdown>
            </div>
          </div>
        )}
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-muted-foreground">Loading SKILL.md…</p>
          </div>
        )}
      </div>
    </div>
  )
}
