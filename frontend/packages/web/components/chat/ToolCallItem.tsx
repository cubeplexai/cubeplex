'use client'

import { useEffect, useRef, useState, memo } from 'react'
import type { MCPToolIcon, PendingConfirm, ToolCallRef } from '@cubebox/core'
import { CheckCircle2, Circle, PanelRight, Plug } from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useMcpToolRegistryStore, useToolDetailStore } from '@cubebox/core'
import { useNowSeconds } from '@/hooks/useNowSeconds'
import { SandboxConfirmCard } from './SandboxConfirmCard'

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
  const mcpIconSrc = mcpEntry ? pickIconSrc(mcpEntry.tool_icons, mcpEntry.server_icons) : null
  const [mcpIconFailed, setMcpIconFailed] = useState(false)
  useEffect(() => {
    setMcpIconFailed(false)
  }, [mcpIconSrc])
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
        {mcpIconSrc && !mcpIconFailed ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={mcpIconSrc}
            alt=""
            className="size-3.5 rounded-sm shrink-0 object-contain"
            onError={() => setMcpIconFailed(true)}
          />
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
