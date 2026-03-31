'use client'

import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message, ContentBlock } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'
import { Bot, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

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

function ToolCallList({ toolCalls }: { toolCalls: { name: string; arguments: Record<string, unknown> }[] }) {
  return (
    <div className="space-y-1">
      {toolCalls.map((tc, i) => (
        <div key={i} className="text-xs font-mono px-2 py-1 rounded bg-muted/40 text-muted-foreground">
          <span className="text-foreground/70">{tc.name}</span>
          {' '}
          <span className="opacity-60">{JSON.stringify(tc.arguments).slice(0, 100)}</span>
        </div>
      ))}
    </div>
  )
}

interface HistoryProps {
  message: Message
  stream?: never
  isStreaming?: never
  statusPhase?: never
}

interface StreamingProps {
  message?: never
  stream: AgentStream
  isStreaming: true
  statusPhase?: string | null
}

type AssistantMessageProps = HistoryProps | StreamingProps

const proseClasses = `prose prose-sm dark:prose-invert max-w-none
  prose-p:leading-relaxed prose-p:my-1
  prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1
  prose-headings:text-foreground
  prose-p:text-foreground prose-li:text-foreground prose-strong:text-foreground
  prose-code:text-foreground prose-code:text-[0.8em] prose-code:bg-muted
  prose-code:px-1 prose-code:py-0.5 prose-code:rounded
  prose-code:before:content-none prose-code:after:content-none
  prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-pre:rounded-lg
  prose-pre:text-[0.8em]
  prose-ul:my-1 prose-ol:my-1 prose-li:my-0
  prose-blockquote:border-l-primary/40 prose-blockquote:text-muted-foreground
  prose-hr:border-border prose-a:text-primary prose-strong:font-semibold
  prose-table:text-foreground prose-th:text-foreground prose-td:text-foreground`

/** Build ordered blocks from legacy flat Message fields (for messages without blocks) */
function blocksFromMessage(msg: Message): ContentBlock[] {
  const result: ContentBlock[] = []
  if (msg.reasoning) result.push({ type: 'reasoning', content: msg.reasoning })
  if (msg.tool_calls) {
    for (const tc of msg.tool_calls) {
      result.push({ type: 'tool_call', name: tc.name, arguments: tc.arguments, tool_call_id: '' })
    }
  }
  if (msg.content) result.push({ type: 'text', content: msg.content })
  return result
}

function ContentBlockRenderer(
  { block, index, isLast, isStreaming }: {
    block: ContentBlock; index: number; isLast: boolean; isStreaming: boolean
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
  if (block.type === 'tool_call') {
    return (
      <div className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ToolCallList toolCalls={[{ name: block.name, arguments: block.arguments }]} />
      </div>
    )
  }
  // text block
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
    </div>
  )
}

/** Group consecutive tool_call blocks for compact rendering */
function groupBlocks(blocks: ContentBlock[]): (ContentBlock | ContentBlock[])[] {
  const result: (ContentBlock | ContentBlock[])[] = []
  for (const block of blocks) {
    if (block.type === 'tool_call') {
      const last = result[result.length - 1]
      if (Array.isArray(last) && last[0].type === 'tool_call') {
        last.push(block)
      } else {
        result.push([block])
      }
    } else {
      result.push(block)
    }
  }
  return result
}

export function AssistantMessage(
  { message, stream, isStreaming, statusPhase }: AssistantMessageProps,
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
            const toolCalls = item.map((b) => {
              const tc = b as ContentBlock & { type: 'tool_call' }
              return { name: tc.name, arguments: tc.arguments }
            })
            return (
              <div key={i} className="bg-card border border-border rounded-xl px-3 py-2.5">
                <ToolCallList toolCalls={toolCalls} />
              </div>
            )
          }
          return (
            <ContentBlockRenderer
              key={i}
              block={item}
              index={i}
              isLast={i === grouped.length - 1}
              isStreaming={isStreaming === true}
            />
          )
        })}
        {!hasContent && isStreaming && (
          <div data-testid="loading-indicator" className="flex items-center gap-1 pl-1">
            {statusPhase === 'sandbox_creating' ? (
              <span className="text-xs text-muted-foreground animate-pulse">
                正在准备沙箱环境...
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
