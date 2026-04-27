'use client'

import useSWR from 'swr'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { SkillContent } from '@cubebox/core'
import { proseClasses } from '@/lib/utils'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface SkillViewProps {
  args: Record<string, unknown>
  result: string | null
  /** Optional explicit skill id; when omitted, falls back to args/result-based rendering. */
  skillId?: string
}

interface SkillResult {
  skill_name: string
  content: string
  version?: string
  loaded: boolean
  error: string | null
}

function parseResult(result: string | null): SkillResult | null {
  if (!result) return null
  try {
    return JSON.parse(result) as SkillResult
  } catch {
    return null
  }
}

const contentFetcher = async (url: string): Promise<SkillContent> => {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) throw new Error(`skill content fetch failed: ${res.status}`)
  return res.json() as Promise<SkillContent>
}

export function SkillView({ args, result, skillId }: SkillViewProps) {
  const { workspaceId } = useWorkspaceContext()
  const skillNameFromArgs = String(args.skill_name ?? '')
  const parsed = parseResult(result)

  // Allow `skill_id` to ride in tool args as a future-friendly path.
  const argsSkillId = typeof args.skill_id === 'string' ? args.skill_id : undefined
  const resolvedSkillId = skillId ?? argsSkillId

  // Catalog fetch is enabled only when both workspaceId + skill id are present.
  const fetchKey =
    workspaceId && resolvedSkillId ? `/api/v1/ws/${workspaceId}/skills/${resolvedSkillId}` : null
  const {
    data: fetched,
    isLoading: fetchLoading,
    error: fetchError,
  } = useSWR<SkillContent>(fetchKey, contentFetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })

  const displayName = fetched?.name ?? parsed?.skill_name ?? skillNameFromArgs
  const displayVersion = fetched?.version ?? parsed?.version ?? null
  const displayContent = fetched?.content ?? parsed?.content ?? ''
  const displayError = parsed?.error ?? (fetchError ? fetchError.message : null)
  // "Loaded" status: prefer the live fetch when active; otherwise the tool-result flag.
  const loaded = fetched ? true : parsed ? parsed.loaded : null

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Skill:</span>
        <span className="font-mono text-sm font-semibold">{displayName}</span>
        {displayVersion && (
          <span className="font-mono text-[11px] text-muted-foreground/80">v{displayVersion}</span>
        )}
        {loaded !== null && (
          <span
            className={`rounded-full px-1.5 py-0.5 text-xs ${
              loaded
                ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                : 'bg-red-500/10 text-red-600 dark:text-red-400'
            }`}
          >
            {loaded ? 'loaded' : 'failed'}
          </span>
        )}
      </div>

      {displayError && (
        <div className="rounded-md bg-red-500/10 p-3 text-sm text-red-600 dark:text-red-400">
          {displayError}
        </div>
      )}

      {fetchLoading && !displayContent && (
        <p className="text-xs text-muted-foreground">加载 SKILL.md…</p>
      )}

      {displayContent && (
        <div className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayContent}</ReactMarkdown>
        </div>
      )}

      {!parsed && !fetched && !fetchLoading && result && (
        <pre className="text-xs whitespace-pre-wrap text-muted-foreground">{result}</pre>
      )}
    </div>
  )
}
