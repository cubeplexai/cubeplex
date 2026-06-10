'use client'

import { useState, useEffect, useRef, memo } from 'react'
import { useTranslations } from 'next-intl'
import { MarkdownWithCitations } from '@/components/shared/MarkdownWithCitations'
import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import type { AgentStream, ContentBlock, ToolCallRef } from '@cubebox/core'
import { ToolCallItem } from './ToolCallItem'
import { AgentAvatar } from './AgentAvatar'
import { proseClasses } from '@/lib/utils'
import { getWriteFileSummary } from '@/lib/writeFilePreview'

interface Props {
  name: string
  role: string
  task: string
  index: number
  agentId?: string
  stream?: AgentStream
  isRunning: boolean
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  conversationId?: string
}

type ToolDisplayBlock = Extract<ContentBlock, { type: 'tool_call' | 'tool_call_streaming' }>

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export const SubAgentCard = memo(function SubAgentCard({
  name,
  role,
  task,
  index,
  agentId,
  stream,
  isRunning,
  toolResultMap,
  conversationId,
}: Props) {
  const t = useTranslations('chat')
  const [expanded, setExpanded] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(Date.now())
  const scrollRef = useRef<HTMLDivElement>(null)

  // Reset start time when component mounts (new agent run)
  useEffect(() => {
    startedAt.current = Date.now()
  }, [])

  // Live elapsed timer
  useEffect(() => {
    if (!isRunning) return
    const tick = () => setElapsed(Date.now() - startedAt.current)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isRunning])

  // Auto-scroll streaming content — track all content changes like ReasoningBlock
  const toolCallCount = stream?.toolCalls.length ?? 0
  const toolResultCount = stream?.toolResults.length ?? 0
  const streamText = stream?.text ?? ''
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [toolCallCount, toolResultCount, streamText, isRunning])

  const toolCalls = stream?.toolCalls ?? []
  const toolBlocks = (stream?.blocks ?? []).filter(
    (block): block is ToolDisplayBlock =>
      block.type === 'tool_call' || block.type === 'tool_call_streaming',
  )
  const visibleToolBlocks: ToolDisplayBlock[] =
    toolBlocks.length > 0
      ? toolBlocks
      : toolCalls.map((tc) => ({
          type: 'tool_call' as const,
          name: tc.data.name,
          arguments: tc.data.arguments,
          id: tc.data.tool_call_id,
        }))
  const completedCount = toolCalls.filter((tc) => toolResultMap[tc.data.tool_call_id]).length
  const pendingTc = toolCalls.find((tc) => !toolResultMap[tc.data.tool_call_id])
  const hasContent = stream && (toolBlocks.length > 0 || toolCalls.length > 0 || stream.text)
  const displayTime = isRunning ? elapsed : hasContent ? elapsed : 0

  const renderToolBlock = (block: ToolDisplayBlock, i: number, showDivider = false) => {
    if (block.type === 'tool_call_streaming') {
      const supportsPreview = block.name === 'write_file'
      return (
        <ToolCallItem
          key={block.tool_call_id ?? `streaming-${i}`}
          name={block.name || 'tool'}
          arguments={{}}
          toolCallId={block.tool_call_id ?? `streaming-${i}`}
          summaryOverride={
            supportsPreview
              ? getWriteFileSummary({}, block.args_text)
              : block.args_text.trim() || undefined
          }
          contentTypeOverride={supportsPreview ? 'write_file' : undefined}
          toolRef={
            supportsPreview
              ? ({
                  agent_id: agentId ?? null,
                  tool_call_id: block.tool_call_id,
                  index: block.index,
                } satisfies ToolCallRef)
              : undefined
          }
          isPending={true}
          allowOpenWhenPending={supportsPreview}
          showDivider={showDivider}
        />
      )
    }

    const result = toolResultMap[block.id] ?? null
    return (
      <ToolCallItem
        key={block.id || i}
        name={block.name}
        arguments={block.arguments}
        toolCallId={block.id}
        contentTypeOverride={block.name === 'write_file' ? 'write_file' : undefined}
        toolRef={
          block.name === 'write_file'
            ? ({
                agent_id: agentId ?? null,
                tool_call_id: block.id,
                index: null,
              } satisfies ToolCallRef)
            : undefined
        }
        toolResult={result}
        isPending={isRunning && !result}
        allowOpenWhenPending={block.name === 'write_file'}
        showDivider={showDivider}
      />
    )
  }

  return (
    <div
      className="border border-border rounded-xl overflow-hidden bg-muted/10 border-l-2
      border-l-primary/40"
    >
      {/* Header */}
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        <AgentAvatar seed={name} size={32} className="rounded-md shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm text-foreground">{name}</span>
            <span
              className="text-xs text-muted-foreground bg-muted/50 px-1.5 py-0.5
              rounded-md"
            >
              {role}
            </span>
            <span className="ml-auto text-xs text-muted-foreground/50 font-mono tabular-nums">
              {String(index).padStart(2, '0')}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{task}</p>
        </div>
      </div>

      {/* Content area — collapsed by default, expandable */}
      {hasContent && (
        <div className="border-t border-border">
          {/* Streaming viewport (always visible when running, shows last few items) */}
          {!expanded && (
            <div
              ref={scrollRef}
              className="overflow-y-auto scrollbar-none"
              style={{
                maxHeight: 'calc(2.5rem * 3)',
                maskImage:
                  'linear-gradient(to bottom, transparent 0%, black 15%, black 80%, transparent 100%)',
                WebkitMaskImage:
                  'linear-gradient(to bottom, transparent 0%, black 15%, black 80%, transparent 100%)',
              }}
            >
              {visibleToolBlocks.map((block, i) => renderToolBlock(block, i))}
              {!isRunning && stream?.text && (
                <div className={`px-3 py-2 text-xs text-muted-foreground line-clamp-3`}>
                  {stream.text.slice(-200)}
                </div>
              )}
            </div>
          )}

          {/* Expanded full content */}
          {expanded && (
            <div className="max-h-80 overflow-y-auto">
              {visibleToolBlocks.map((block, i) => renderToolBlock(block, i, i > 0))}
              {stream?.text && (
                <div className={`px-3 py-2 border-t border-border ${proseClasses}`}>
                  <MarkdownWithCitations conversationId={conversationId}>
                    {stream.text}
                  </MarkdownWithCitations>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Footer: activity dots + expand toggle + elapsed time */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-border">
        {/* Activity dots */}
        <div className="flex items-center gap-1">
          {Array.from({ length: completedCount }, (_, i) => (
            <span key={`done-${i}`} className="w-1.5 h-1.5 rounded-full bg-success-solid" />
          ))}
          {isRunning && pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-info-solid animate-pulse" />
          )}
          {isRunning && !pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
          )}
        </div>

        {/* Expand/collapse toggle */}
        {hasContent && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-1 flex items-center gap-0.5 text-xs text-muted-foreground
              hover:text-foreground transition-colors"
          >
            {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <span>{expanded ? t('collapse') : t('expand')}</span>
          </button>
        )}

        {/* Running indicator + elapsed time */}
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          {isRunning && (
            <span className="flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full bg-muted-foreground animate-pulse"
                  style={{ animationDelay: `${i * 200}ms` }}
                />
              ))}
            </span>
          )}
          {!isRunning && hasContent && <CheckCircle2 className="size-3 text-success-fg" />}
          {displayTime >= 1000 && <span>{formatDuration(displayTime)}</span>}
        </span>
      </div>

      {/* Empty running state */}
      {!hasContent && isRunning && (
        <div className="px-3 pb-2.5">
          <span className="text-xs text-muted-foreground animate-pulse">{t('executing')}</span>
        </div>
      )}
    </div>
  )
})
