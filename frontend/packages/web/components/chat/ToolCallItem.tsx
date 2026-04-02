'use client'

import { useState, useEffect, useRef, memo } from 'react'
import {
  CheckCircle2,
  Circle,
  PanelRight,
} from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useToolDetailStore } from '@cubebox/core'

interface ToolCallItemProps {
  name: string
  arguments: Record<string, unknown>
  toolCallId: string
  toolResult?: {
    content: string
    receivedAt: number
    contentType?: string
  } | null
  timestamp?: string
  /** True while this tool is still executing */
  isPending: boolean
  /** Show border-top separator (not first in group) */
  showDivider?: boolean
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
  toolCallId,
  toolResult,
  timestamp,
  isPending,
  showDivider,
}: ToolCallItemProps) {
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(Date.now())
  const openPanel = useToolDetailStore((s) => s.open)

  useEffect(() => {
    if (timestamp) {
      startedAt.current = new Date(timestamp).getTime()
    }
  }, [timestamp])

  // Live timer while pending
  useEffect(() => {
    if (!isPending) return
    const tick = () =>
      setElapsed(Date.now() - startedAt.current)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isPending])

  const duration = toolResult
    ? toolResult.receivedAt - startedAt.current
    : elapsed

  const Icon = getToolIcon(name)
  const summary = getParamSummary(name, args)

  const handleViewInPanel = () => {
    openPanel(name, args, toolResult?.content ?? null, toolResult?.contentType)
  }

  return (
    <div
      className={
        showDivider ? 'border-t border-border' : ''
      }
    >
      <button
        type="button"
        onClick={toolResult ? handleViewInPanel : undefined}
        className={`flex w-full items-center gap-2 px-3
          py-2 text-sm transition-colors
          ${toolResult ? 'hover:bg-muted/50 cursor-pointer' : ''}`}
      >
        <Icon
          className="size-3.5 text-muted-foreground
            shrink-0"
        />
        <span
          className="font-medium text-foreground
            shrink-0"
        >
          {name}
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
          {isPending ? (
            <>
              <Circle
                className="size-2.5 text-blue-500
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
              <CheckCircle2
                className="size-3 text-emerald-500"
              />
              <span
                className="text-xs
                  text-muted-foreground"
              >
                {formatDuration(duration)}
              </span>
              <PanelRight
                className="size-3 text-muted-foreground"
              />
            </>
          ) : null}
        </span>
      </button>
    </div>
  )
})
