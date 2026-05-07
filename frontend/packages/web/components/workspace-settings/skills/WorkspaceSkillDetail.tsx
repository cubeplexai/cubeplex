'use client'

import { useState } from 'react'
import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText } from 'lucide-react'
import {
  createApiClient,
  deleteWorkspaceSkill,
  installWorkspaceSkill,
  toggleWorkspaceSkill,
  type SkillContent,
} from '@cubebox/core'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn, proseClasses } from '@/lib/utils'
import type { WorkspaceSkillEntry, WorkspaceSkillState } from '@/hooks/useWorkspaceSkillsCatalog'

interface WorkspaceSkillDetailProps {
  wsId: string
  skill: WorkspaceSkillEntry
  onActionDone: () => void
}

function stripFrontmatter(content: string): string {
  return content.replace(/^---\s*\n[\s\S]*?\n---\s*(\n|$)/, '')
}

const STATE_LABEL: Record<WorkspaceSkillState, string> = {
  'org-enabled': 'Enabled',
  'org-disabled': 'Disabled',
  'workspace-private': 'Workspace-private',
  available: 'Available',
}

const STATE_BADGE_VARIANT: Record<WorkspaceSkillState, { className: string }> = {
  'org-enabled': { className: 'border-emerald-500/40 text-emerald-600' },
  'org-disabled': { className: 'text-muted-foreground' },
  'workspace-private': { className: 'border-primary/40 text-primary' },
  available: { className: 'border-amber-500/40 text-amber-600' },
}

async function contentFetcher(url: string): Promise<SkillContent> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`)
  return res.json() as Promise<SkillContent>
}

function WorkspaceActions({
  wsId,
  skill,
  onDone,
}: {
  wsId: string
  skill: WorkspaceSkillEntry
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function client() {
    const c = createApiClient('')
    c.setWorkspaceId(wsId)
    return c
  }

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      await action()
      onDone()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-destructive">{error}</span>}
      {skill.workspaceState === 'org-enabled' && skill.installId && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void run(() => toggleWorkspaceSkill(client(), skill.installId!, false))}
        >
          {busy ? 'Disabling…' : 'Disable in workspace'}
        </Button>
      )}
      {skill.workspaceState === 'org-disabled' && skill.installId && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() => void run(() => toggleWorkspaceSkill(client(), skill.installId!, true))}
        >
          {busy ? 'Enabling…' : 'Enable in workspace'}
        </Button>
      )}
      {skill.workspaceState === 'workspace-private' && skill.installId && (
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => void run(() => deleteWorkspaceSkill(client(), skill.installId!))}
        >
          {busy ? 'Removing…' : 'Remove from workspace'}
        </Button>
      )}
      {skill.workspaceState === 'available' && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() =>
            void run(() => installWorkspaceSkill(client(), skill.id, skill.current_version))
          }
        >
          {busy ? 'Installing…' : 'Install in workspace'}
        </Button>
      )}
    </div>
  )
}

export function WorkspaceSkillDetail({ wsId, skill, onActionDone }: WorkspaceSkillDetailProps) {
  const targetVersion = skill.installed_version ?? skill.current_version
  const contentKey = `/api/v1/ws/${wsId}/skills/${skill.id}?version=${encodeURIComponent(targetVersion)}`
  const { data: content, isLoading } = useSWR<SkillContent>(contentKey, contentFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  return (
    <div className="flex w-full flex-col gap-4 p-6">
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-xl font-semibold tracking-tight">{skill.name}</h3>
          <Badge variant="outline" className="font-mono">
            v{targetVersion}
          </Badge>
          <Badge variant={skill.source === 'preinstalled' ? 'default' : 'secondary'}>
            {skill.source === 'preinstalled' ? 'Preinstalled' : 'Org-uploaded'}
          </Badge>
          <Badge
            variant="outline"
            className={cn(STATE_BADGE_VARIANT[skill.workspaceState].className)}
          >
            {STATE_LABEL[skill.workspaceState]}
          </Badge>
          <div className="ml-auto">
            <WorkspaceActions wsId={wsId} skill={skill} onDone={onActionDone} />
          </div>
        </div>
        {skill.description && (
          <p className="text-sm leading-relaxed text-muted-foreground">{skill.description}</p>
        )}
        {skill.keywords.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {skill.keywords.map((kw) => (
              <Badge key={kw} variant="outline" className="text-[11px]">
                {kw}
              </Badge>
            ))}
          </div>
        )}
      </header>

      <Tabs defaultValue="overview" className="flex-1 flex-col">
        <TabsList variant="line" className="w-full justify-start border-b border-border/60 pb-0">
          <TabsTrigger value="overview">
            <FileText className="size-3.5" />
            Overview
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          {isLoading && <p className="text-xs text-muted-foreground">Loading content…</p>}
          {content && (
            <div className="rounded-lg border border-border/70 bg-card/40 px-4 py-3">
              <div className={proseClasses}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {stripFrontmatter(content.content)}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
