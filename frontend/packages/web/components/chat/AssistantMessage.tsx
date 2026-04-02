'use client'

import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message, ContentBlock, SubagentSummary } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'
import { Bot, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { SubAgentCard } from './SubAgentCard'
import { ToolCallGroup } from './ToolCallGroup'

interface ReasoningBlockProps {
  reasoning: string
  isStreaming: boolean
  startedAt?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainSeconds = seconds % 60
  return remainSeconds > 0 ? `${minutes}分${remainSeconds}秒` : `${minutes}分`
}

function ReasoningBlock({ reasoning, isStreaming, startedAt, durationMs }: ReasoningBlockProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const prevStreamingRef = useRef(isStreaming)

  // Sync open state with isStreaming transitions
  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) {
      setIsOpen(true)
    } else if (!isStreaming && prevStreamingRef.current) {
      setIsOpen(false)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  // Open on first render if streaming
  useEffect(() => {
    if (isStreaming) setIsOpen(true)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Live elapsed timer while streaming
  useEffect(() => {
    if (!isStreaming || !startedAt) return
    const tick = () => setElapsed(Date.now() - startedAt)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isStreaming, startedAt])

  // Duration: finalized > live elapsed > nothing
  const displayTime = durationMs ?? (isStreaming && startedAt ? elapsed : null)

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground
        hover:text-foreground transition-colors group">
        <span className="text-muted-foreground/60 group-hover:text-muted-foreground
          transition-colors">
          {isOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>
        <Brain className="size-3 text-muted-foreground/70" />
        <span>{isStreaming ? '思考中...' : '思考过程'}</span>
        {displayTime != null && displayTime >= 1000 && (
          <span className="text-muted-foreground/50 ml-0.5">
            {formatDuration(displayTime)}
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5">
        <div className="pl-4 border-l-2 border-border/50">
          <p className="text-xs text-muted-foreground/70 leading-relaxed whitespace-pre-wrap
            italic">
            {reasoning}
          </p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}


interface HistoryProps {
  message: Message
  subagentDataMap?: Record<string, SubagentSummary>
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  stream?: never
  isStreaming?: never
  statusPhase?: never
  subAgentStreams?: never
}

interface StreamingProps {
  message?: never
  subagentDataMap?: never
  toolResultMap: Record<string, { content: string; receivedAt: number }>
  stream: AgentStream
  isStreaming: true
  statusPhase?: string | null
  subAgentStreams?: Record<string, AgentStream>
}

type AssistantMessageProps = HistoryProps | StreamingProps

import { proseClasses } from '@/lib/utils'

/** Build ordered blocks from legacy flat Message fields (for messages without blocks) */
function blocksFromMessage(msg: Message): ContentBlock[] {
  const result: ContentBlock[] = []
  if (msg.reasoning) result.push({ type: 'reasoning', content: msg.reasoning })
  if (msg.tool_calls) {
    for (const tc of msg.tool_calls) {
      result.push({
        type: 'tool_call',
        name: tc.name,
        arguments: tc.arguments,
        tool_call_id: tc.tool_call_id ?? '',
      })
    }
  }
  if (msg.content) result.push({ type: 'text', content: msg.content })
  return result
}

/** Convert a consolidated SubagentSummary to an AgentStream for SubAgentCard */
function subagentSummaryToStream(summary: SubagentSummary): AgentStream {
  return {
    text: summary.text,
    toolCalls: summary.tool_calls.map((tc, i) => ({
      type: 'tool_call' as const,
      timestamp: '',
      data: { tool_call_id: `hist-${i}`, name: tc.name, arguments: tc.arguments },
      agent_id: null,
      agent_name: null,
    })),
    toolResults: [],
    reasoning: summary.reasoning,
    blocks: [],
    name: null,
  }
}

function ContentBlockRenderer(
  { block, index, isLast, isStreaming, subAgentStreams, subagentDataMap, toolResultMap }: {
    block: ContentBlock; index: number; isLast: boolean; isStreaming: boolean
    subAgentStreams?: Record<string, AgentStream>
    subagentDataMap?: Record<string, SubagentSummary>
    toolResultMap: Record<string, { content: string; receivedAt: number }>
  },
) {
  if (block.type === 'reasoning') {
    return (
      <div className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ReasoningBlock
          reasoning={block.content}
          isStreaming={isStreaming && isLast}
          startedAt={block.started_at}
          durationMs={block.duration_ms}
        />
      </div>
    )
  }
  if (block.type === 'tool_call' && block.name === 'subagent') {
    const agentKey = `subagent:${block.tool_call_id}`
    const stream = subAgentStreams?.[agentKey]
    // For historical messages, construct stream from consolidated data
    const historicalStream = !stream && subagentDataMap?.[agentKey]
      ? subagentSummaryToStream(subagentDataMap[agentKey])
      : undefined
    const displayName =
      (block.arguments as { name?: string }).name ?? 'Subagent'
    return (
      <SubAgentCard
        name={displayName}
        stream={stream ?? historicalStream}
        isRunning={isStreaming && !!stream}
        toolResultMap={toolResultMap}
      />
    )
  }
  if (block.type === 'tool_call') {
    return (
      <ToolCallGroup
        blocks={[block as ContentBlock & { type: 'tool_call' }]}
        toolResultMap={toolResultMap}
        isStreaming={isStreaming}
      />
    )
  }
  // text block
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
    </div>
  )
}

/** Group consecutive tool_call blocks for compact rendering (subagent calls render individually) */
function groupBlocks(blocks: ContentBlock[]): (ContentBlock | ContentBlock[])[] {
  const result: (ContentBlock | ContentBlock[])[] = []
  for (const block of blocks) {
    if (
      block.type === 'tool_call' &&
      block.name !== 'subagent' &&
      block.name !== 'write_todos'
    ) {
      const last = result[result.length - 1]
      if (Array.isArray(last) && last[0].type === 'tool_call'
        && (last[0] as ContentBlock & { name: string }).name !== 'subagent') {
        last.push(block)
      } else {
        result.push([block])
      }
    } else if (
      block.type === 'tool_call' &&
      block.name === 'write_todos'
    ) {
      continue
    } else {
      result.push(block)
    }
  }
  return result
}

export function AssistantMessage(
  { message, stream, isStreaming, statusPhase, subAgentStreams, subagentDataMap, toolResultMap }:
  AssistantMessageProps,
) {
  const blocks: ContentBlock[] = isStreaming
    ? stream.blocks
    : (message.blocks ?? blocksFromMessage(message))

  const hasContent = blocks.length > 0
  const grouped = groupBlocks(blocks)

  return (
    <div data-role="assistant" className="flex justify-start gap-2.5">
      <div className="shrink-0 w-6 h-6 rounded-md border border-border bg-card
        flex items-center justify-center mt-0.5">
        <Bot className="size-3.5 text-primary/70" />
      </div>
      <div className="flex-1 max-w-[75%] space-y-2">
        {grouped.map((item, i) => {
          if (Array.isArray(item)) {
            // grouped tool_call blocks
            const tcBlocks = item as (ContentBlock & { type: 'tool_call' })[]
            return (
              <ToolCallGroup
                key={i}
                blocks={tcBlocks}
                toolResultMap={toolResultMap}
                isStreaming={isStreaming === true}
              />
            )
          }
          return (
            <ContentBlockRenderer
              key={i}
              block={item}
              index={i}
              isLast={i === grouped.length - 1}
              isStreaming={isStreaming === true}
              subAgentStreams={subAgentStreams}
              subagentDataMap={subagentDataMap}
              toolResultMap={toolResultMap}
            />
          )
        })}
        {!hasContent && isStreaming && (
          <div data-testid="loading-indicator" className="flex items-center gap-1 pl-1">
            {statusPhase === 'sandbox_creating' ? (
              <span className="text-xs text-muted-foreground animate-pulse">
                正在准备沙箱环境...
              </span>
            ) : statusPhase === 'sandbox_failed' ? (
              <span className="text-xs text-destructive">
                沙箱环境创建失败，将在无沙箱模式下继续
              </span>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-primary
                  animate-bounce [animation-delay:300ms]" />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
