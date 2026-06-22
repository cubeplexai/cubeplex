'use client'

import { useEffect, useRef, memo } from 'react'
import type { MCPToolIcon, PendingConfirm, ToolCallRef } from '@cubebox/core'
import { CheckCircle2, Circle, PanelRight, Plug } from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore, useToolDetailStore } from '@cubebox/core'
import { useNowSeconds } from '@/hooks/useNowSeconds'
import { SandboxConfirmCard } from './SandboxConfirmCard'

/** Pick the best icon variant: prefer per-tool over server icon; fall back
 * to the first entry when nothing else matches. Theme matching is best-effort
 * — the dom doesn't expose the current color scheme cleanly here, and the
 * spec says clients ignorant of theme should pick the first entry. */
function pickIcon(toolIcons: MCPToolIcon[], serverIcons: MCPToolIcon[]): MCPToolIcon | null {
  if (toolIcons.length > 0) return toolIcons[0]
  if (serverIcons.length > 0) return serverIcons[0]
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
  /** Show border-top separator (not first in group) */
  showDivider?: boolean
  pendingConfirm?: PendingConfirm | null
  onSandboxConfirm?: (decision: 'approve' | 'deny') => Promise<void>
}

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

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
  showDivider,
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
  const displayName = mcpEntry?.bare_name ?? name
  const mcpIcon = mcpEntry ? pickIcon(mcpEntry.tool_icons, mcpEntry.server_icons) : null
  const FallbackIcon = getToolIcon(displayName)
  const summary = summaryOverride ?? getParamSummary(displayName, args)
  const canOpen = Boolean(toolResult) || allowOpenWhenPending
  const labelTooltip = mcpEntry ? `${mcpEntry.server_name} · ${mcpEntry.bare_name}` : displayName

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
    <div className={showDivider ? 'border-t border-border' : ''}>
      <button
        type="button"
        onClick={canOpen ? handleViewInPanel : undefined}
        title={labelTooltip}
        className={`flex w-full items-center gap-2 px-3
          py-2 text-sm transition-colors
          ${canOpen ? 'hover:bg-accent cursor-pointer' : ''}`}
      >
        {mcpIcon ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={mcpIcon.src} alt="" className="size-3.5 rounded-sm shrink-0 object-contain" />
        ) : mcpEntry ? (
          <Plug className="size-3.5 text-muted-foreground shrink-0" />
        ) : (
          <FallbackIcon className="size-3.5 text-muted-foreground shrink-0" />
        )}
        <span
          className="font-medium text-foreground
            shrink-0"
        >
          {displayName}
        </span>
        {summary && (
          <>
            <span
              className="text-muted-foreground/40
                shrink-0"
            >
              |
            </span>
            <span
              className="text-xs text-muted-foreground
                truncate"
            >
              {summary}
            </span>
          </>
        )}
        <span
          className="ml-auto flex items-center gap-1.5
            shrink-0"
        >
          {pendingConfirm ? null : isPending ? (
            <>
              <Circle
                className="size-2.5 text-info-fg
                  animate-pulse"
              />
              <span
                className="text-xs
                  text-muted-foreground"
              >
                {formatDuration(elapsed)}
              </span>
            </>
          ) : toolResult ? (
            <>
              <CheckCircle2 className="size-3 text-success-fg" />
              <span
                className="text-xs
                  text-muted-foreground"
              >
                {formatDuration(duration)}
              </span>
              <PanelRight className="size-3 text-muted-foreground" />
            </>
          ) : canOpen ? (
            <PanelRight className="size-3 text-muted-foreground" />
          ) : null}
        </span>
      </button>
      {pendingConfirm && onSandboxConfirm && (
        <SandboxConfirmCard
          pending={pendingConfirm}
          onApprove={() => onSandboxConfirm('approve')}
          onDeny={() => onSandboxConfirm('deny')}
        />
      )}
    </div>
  )
})
