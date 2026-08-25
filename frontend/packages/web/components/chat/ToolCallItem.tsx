'use client'

import { useEffect, useRef, useState, memo } from 'react'
import type { MCPToolIcon, PendingConfirm, ToolCallRef } from '@cubeplex/core'
import { humanizeToolName, splitToolName } from '@cubeplex/core'
import { Check, Loader2, Plug } from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore, useToolDetailStore } from '@cubeplex/core'
import { useNowSeconds } from '@/hooks/useNowSeconds'
import { SandboxConfirmCard } from './SandboxConfirmCard'
import { cn } from '@/lib/utils'

/** Pick the best renderable icon src: prefer per-tool over server icon;
 * prefer cached_src (offline data URI) over remote src. Theme matching is
 * best-effort — the dom doesn't expose the current color scheme cleanly
 * here, and the spec says clients ignorant of theme should pick the first
 * entry. */
function pickIconSrc(toolIcons: MCPToolIcon[], serverIcons: MCPToolIcon[]): string | null {
  const allowRemote = (() => {
    const raw = process.env.NEXT_PUBLIC_MCP_ALLOW_REMOTE_ICONS
    if (raw === undefined || raw === '') return true
    return raw !== '0' && raw.toLowerCase() !== 'false'
  })()
  for (const icon of [...toolIcons, ...serverIcons]) {
    if (icon.cached_src) return icon.cached_src
    const src = icon.src
    if (!src) continue
    if (src.startsWith('data:image/') || src.startsWith('/')) return src
    if (allowRemote && (src.startsWith('https://') || src.startsWith('http://'))) return src
  }
  return null
}

interface ToolCallItemProps {
  name: string
  arguments: Record<string, unknown>
  toolCallId: string
  summaryOverride?: string
  contentTypeOverride?: string
  toolRef?: ToolCallRef
  toolResult?: {
    content: string
    receivedAt: number
    startedAt?: number
    contentType?: string
  } | null
  timestamp?: string
  /** True while this tool is still executing */
  isPending: boolean
  allowOpenWhenPending?: boolean
  /** @deprecated chips use gap, not dividers — kept for call-site compat */
  showDivider?: boolean
  pendingConfirm?: PendingConfirm | null
  onSandboxConfirm?: (decision: 'approve' | 'deny') => Promise<void>
}

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  if (ms < 1000) return `${(ms / 1000).toFixed(1)}s`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

/**
 * Compact Beautiful-UI-style tool chip. One dense line; click opens the
 * right-hand tool detail panel (unchanged behaviour).
 */
export const ToolCallItem = memo(function ToolCallItem({
  name,
  arguments: args,
  toolCallId: _toolCallId,
  summaryOverride,
  contentTypeOverride,
  toolRef,
  toolResult,
  timestamp,
  isPending,
  allowOpenWhenPending,
  pendingConfirm,
  onSandboxConfirm,
}: ToolCallItemProps) {
  const startedAt = useRef(timestamp ? new Date(timestamp).getTime() : Date.now())
  const openPanel = useToolDetailStore((s) => s.open)

  useEffect(() => {
    if (timestamp) {
      startedAt.current = new Date(timestamp).getTime()
    }
  }, [timestamp])

  const nowMs = useNowSeconds(isPending)
  const elapsed = isPending ? Math.max(0, nowMs - startedAt.current) : 0

  const duration = toolResult
    ? toolResult.receivedAt - (toolResult.startedAt ?? startedAt.current)
    : elapsed

  const mcpEntry = useMcpToolRegistryStore((s) => s.lookup(name))
  const nameParts = splitToolName(name)
  const isMcpTool = Boolean(mcpEntry) || nameParts.server !== null
  const displayName = humanizeToolName(mcpEntry?.bare_name ?? nameParts.tool)
  const serverName =
    mcpEntry?.server_name ?? (nameParts.server ? humanizeToolName(nameParts.server) : null)
  const mcpIconSrc = mcpEntry ? pickIconSrc(mcpEntry.tool_icons, mcpEntry.server_icons) : null
  const [mcpIconFailed, setMcpIconFailed] = useState(false)
  useEffect(() => {
    setMcpIconFailed(false)
  }, [mcpIconSrc])
  const toolIdentifier = mcpEntry?.bare_name ?? nameParts.tool
  const FallbackIcon = getToolIcon(toolIdentifier)
  const summary = summaryOverride ?? getParamSummary(toolIdentifier, args)
  const canOpen = Boolean(toolResult) || allowOpenWhenPending
  const labelTooltip = mcpEntry
    ? `${mcpEntry.server_name} · ${mcpEntry.bare_name}`
    : serverName
      ? `${serverName} · ${displayName}${summary ? ` — ${summary}` : ''}`
      : summary
        ? `${displayName} — ${summary}`
        : displayName

  const handleViewInPanel = () => {
    openPanel(
      name,
      args,
      toolResult?.content ?? null,
      contentTypeOverride ?? toolResult?.contentType,
      toolRef,
    )
  }

  return (
    <div>
      <button
        type="button"
        onClick={canOpen ? handleViewInPanel : undefined}
        disabled={!canOpen}
        title={labelTooltip}
        className={cn(
          'group flex w-full max-w-full items-center gap-2 rounded-lg border border-transparent px-2 py-1',
          'text-left text-[12px] leading-5 transition-colors',
          canOpen
            ? 'cursor-pointer text-muted-foreground hover:border-border/60 hover:bg-muted/55 hover:text-foreground'
            : 'cursor-default text-muted-foreground',
        )}
      >
        {mcpIconSrc && !mcpIconFailed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={mcpIconSrc}
            alt=""
            className="size-4 shrink-0 rounded-[4px] object-contain opacity-90"
            onError={() => setMcpIconFailed(true)}
          />
        ) : mcpEntry ? (
          <Plug className="size-3.5 shrink-0 opacity-70" />
        ) : (
          <FallbackIcon className="size-3.5 shrink-0 opacity-70" />
        )}

        <span className="min-w-0 flex-1 truncate">
          {isMcpTool && serverName && (
            <span className="mr-1.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/60">
              {serverName}
            </span>
          )}
          <span className={cn('font-medium', isPending ? 'text-foreground' : 'text-foreground/90')}>
            {displayName}
          </span>
          {summary && <span className="ml-2 text-muted-foreground/70">{summary}</span>}
        </span>

        <span className="ml-auto flex shrink-0 items-center gap-1 tabular-nums text-[11px] text-muted-foreground/70">
          {pendingConfirm ? null : isPending ? (
            <>
              <Loader2 className="size-3 animate-spin opacity-70" />
              <span>{formatDuration(elapsed)}</span>
            </>
          ) : toolResult ? (
            <>
              <Check className="size-3 text-success-fg" strokeWidth={2.5} />
              {/* Always show completed duration (incl. sub-500ms). The old
                  `>= 500` gate made fast tools flash a live clock then lose
                  the number the moment the result landed. */}
              {Number.isFinite(duration) && duration >= 0 && (
                <span>{formatDuration(duration)}</span>
              )}
            </>
          ) : null}
        </span>
      </button>
      {pendingConfirm && onSandboxConfirm && (
        <div className="mt-1">
          <SandboxConfirmCard
            pending={pendingConfirm}
            onApprove={() => onSandboxConfirm('approve')}
            onDeny={() => onSandboxConfirm('deny')}
          />
        </div>
      )}
    </div>
  )
})
