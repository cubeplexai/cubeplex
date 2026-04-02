'use client'

import { useState, useEffect, useRef } from 'react'
import {
  ChevronRight,
  ChevronDown,
  Clock,
  CheckCircle2,
  Circle,
  PanelRight,
} from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useToolDetailStore } from '@cubebox/core'

interface ToolCallItemProps {
  name: string
  arguments: Record<string, unknown>
  toolCallId: string
  toolResult?: {
    content: string
    receivedAt: number
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

export function ToolCallItem({
  name,
  arguments: args,
  toolCallId,
  toolResult,
  timestamp,
  isPending,
  showDivider,
}: ToolCallItemProps) {
  const [isOpen, setIsOpen] = useState(false)
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
    openPanel(name, args, toolResult?.content ?? null)
  }

  // Truncate result for inline preview
  const resultLines =
    toolResult?.content.split('\n') ?? []
  const showTruncated = resultLines.length > 10
  const previewText = showTruncated
    ? resultLines.slice(0, 6).join('\n') + '\n...'
    : (toolResult?.content ?? '')

  return (
    <div
      className={
        showDivider ? 'border-t border-border' : ''
      }
    >
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-2 px-3
            py-2 text-sm hover:bg-muted/50
            transition-colors cursor-pointer"
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
              </>
            ) : null}
            {isOpen ? (
              <ChevronDown
                className="size-3.5
                  text-muted-foreground"
              />
            ) : (
              <ChevronRight
                className="size-3.5
                  text-muted-foreground"
              />
            )}
          </span>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="px-3 pb-3 space-y-2">
            {toolResult && (
              <>
                <div
                  className="flex items-center gap-1.5
                    text-xs text-muted-foreground"
                >
                  <Clock className="size-3" />
                  <span>
                    {formatDuration(duration)}
                  </span>
                </div>
                <div
                  className="bg-muted rounded-md p-2
                    max-h-48 overflow-auto"
                >
                  <pre
                    className="font-mono text-xs
                      text-foreground whitespace-pre-wrap
                      break-all"
                  >
                    {previewText}
                  </pre>
                </div>
                {showTruncated && (
                  <button
                    onClick={handleViewInPanel}
                    className="flex items-center gap-1
                      text-xs text-primary
                      hover:underline cursor-pointer"
                  >
                    <PanelRight className="size-3" />
                    View in panel
                  </button>
                )}
              </>
            )}
            {!toolResult && isPending && (
              <span
                className="text-xs text-muted-foreground
                  animate-pulse"
              >
                Executing...
              </span>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
