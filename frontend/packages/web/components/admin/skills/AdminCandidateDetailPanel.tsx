'use client'

import { useState } from 'react'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ExternalLink, FileText, ShieldAlert, ShieldCheck, ShieldOff } from 'lucide-react'
import {
  useAdminSkillsStore,
  type SkillCandidateOut,
  type SkillPreviewResponse,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { jsonHeaders } from '@/lib/csrf'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn, proseClasses } from '@/lib/utils'

function TrustInfo({ trust }: { trust: SkillCandidateOut['trust'] }) {
  if (trust === 'official') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-success-surface px-2 py-1 text-xs font-medium text-success-fg">
        <ShieldCheck className="size-3.5" />
        Official
      </span>
    )
  }
  if (trust === 'community') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-info-surface px-2 py-1 text-xs font-medium text-info-fg">
        <ShieldAlert className="size-3.5" />
        Community
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-warning-surface px-2 py-1 text-xs font-medium text-warning-fg">
      <ShieldOff className="size-3.5" />
      Unvetted
    </span>
  )
}

async function previewFetcher(url: string): Promise<SkillPreviewResponse> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillPreviewResponse>
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

interface AdminCandidateDetailPanelProps {
  candidate: SkillCandidateOut
  onInstalled: () => void
}

export function AdminCandidateDetailPanel({
  candidate,
  onInstalled,
}: AdminCandidateDetailPanelProps) {
  const install = useAdminSkillsStore((s) => s.install)
  const installing = useAdminSkillsStore((s) => s.installing[candidate.candidate_id] ?? false)
  const [installError, setInstallError] = useState<string | null>(null)

  const isInCatalog = candidate.install_state === 'in_catalog'

  const { data: preview, isLoading } = useSWR<SkillPreviewResponse>(
    `/api/v1/admin/skills/discover/preview?candidate_id=${candidate.candidate_id}`,
    previewFetcher,
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  async function handleInstall() {
    setInstallError(null)
    try {
      await install(candidate.candidate_id, jsonHeaders())
      onInstalled()
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex w-full flex-col gap-4 overflow-y-auto p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{candidate.name}</h3>
          {candidate.version && (
            <Badge variant="outline" className="font-mono">
              v{candidate.version}
            </Badge>
          )}
          <Badge variant="secondary">{candidate.source_name}</Badge>
          <TrustInfo trust={candidate.trust} />
          <div className="ml-auto flex flex-col items-end gap-1.5">
            <Button
              size="sm"
              disabled={installing || isInCatalog}
              onClick={() => void handleInstall()}
            >
              {isInCatalog ? 'In Catalog' : installing ? 'Adding…' : 'Add to Catalog'}
            </Button>
            {installError && (
              <p className="max-w-48 text-right text-[11px] leading-tight text-destructive">
                {installError}
              </p>
            )}
          </div>
        </div>
        {candidate.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{candidate.description}</p>
        )}
        {candidate.keywords.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {candidate.keywords.map((kw) => (
              <Badge key={kw} variant="outline" className="text-[11px]">
                {kw}
              </Badge>
            ))}
          </div>
        )}
      </header>

      <dl className="flex flex-col gap-3 border-b border-border pb-4">
        {candidate.install_count !== null && (
          <div className="flex items-center gap-3">
            <dt className="min-w-24 text-xs font-medium text-muted-foreground">Downloads</dt>
            <dd className="text-sm">{candidate.install_count.toLocaleString()}</dd>
          </div>
        )}
        {candidate.repo &&
          (() => {
            let safeUrl: string | null = null
            try {
              safeUrl = new URL(candidate.repo).protocol === 'https:' ? candidate.repo : null
            } catch {
              /* */
            }
            return (
              <div className="flex items-center gap-3">
                <dt className="min-w-24 text-xs font-medium text-muted-foreground">Repo</dt>
                <dd className="min-w-0 flex-1">
                  {safeUrl ? (
                    <a
                      href={safeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 truncate text-xs text-muted-foreground hover:text-foreground"
                    >
                      <span className="truncate">
                        {candidate.repo.replace('https://github.com/', '')}
                      </span>
                      <ExternalLink className="size-3 shrink-0" />
                    </a>
                  ) : (
                    <span className="truncate text-xs text-muted-foreground">{candidate.repo}</span>
                  )}
                </dd>
              </div>
            )
          })()}
        {preview?.env_vars && preview.env_vars.length > 0 && (
          <div className="flex items-start gap-3">
            <dt className="min-w-24 pt-0.5 text-xs font-medium text-muted-foreground">
              Requires env
            </dt>
            <dd className="flex flex-wrap gap-1">
              {preview.env_vars.map((v) => (
                <code
                  key={v}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                >
                  {v}
                </code>
              ))}
            </dd>
          </div>
        )}
      </dl>

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            Overview
          </TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4">
          {isLoading && <p className="text-xs text-muted-foreground">Loading SKILL.md…</p>}
          {preview && (
            <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
              <div className={cn(proseClasses)}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {stripFrontmatter(preview.content)}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
