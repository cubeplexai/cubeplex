'use client'

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message, AgentEvent } from '@cubebox/core'
import { ExecutionDetails } from './ExecutionDetails'
import { Bot, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

function extractText(events: AgentEvent[] | null): string {
  if (!events) return ''
  // text_delta events are incremental — concatenate all chunks
  return events
    .filter((e) => e.type === 'text_delta')
    .map((e) => e.data?.content ?? '')
    .join('')
}

function extractReasoning(events: AgentEvent[] | null): string {
  if (!events) return ''
  // reasoning events are incremental — concatenate all chunks
  return events
    .filter((e) => e.type === 'reasoning')
    .map((e) => e.data?.content ?? '')
    .join('')
}

function hasToolActivity(events: AgentEvent[] | null): boolean {
  if (!events) return false
  return events.some((e) => e.type === 'tool_call' || e.type === 'tool_result' || e.type === 'error')
}

interface ReasoningBlockProps {
  reasoning: string
  isStreaming: boolean
}

function ReasoningBlock({ reasoning, isStreaming }: ReasoningBlockProps) {
  const [isOpen, setIsOpen] = useState(isStreaming)

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors group">
        <span className="text-muted-foreground/60 group-hover:text-muted-foreground transition-colors">
          {isOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>
        <Brain className="size-3 text-muted-foreground/70" />
        <span>{isStreaming ? '思考中...' : '思考过程'}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5">
        <div className="pl-4 border-l-2 border-border/50">
          <p className="text-xs text-muted-foreground/70 leading-relaxed whitespace-pre-wrap italic">
            {reasoning}
          </p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

interface AssistantMessageProps {
  message?: Message
  streamingEvents?: AgentEvent[]
  isStreaming?: boolean
}

export function AssistantMessage({
  message,
  streamingEvents = [],
  isStreaming = false,
}: AssistantMessageProps) {
  const events = message?.events ?? streamingEvents
  const text = extractText(events)
  const reasoning = extractReasoning(events)
  const showExecutionPanel = hasToolActivity(events) || (isStreaming && !text && !reasoning)

  return (
    <div className="flex justify-start gap-2.5">
      <div className="shrink-0 w-6 h-6 rounded-md border border-border bg-card flex items-center justify-center mt-0.5">
        <Bot className="size-3.5 text-primary/70" />
      </div>
      <div className="flex-1 max-w-[75%] space-y-2">
        {showExecutionPanel && events && events.length > 0 && (
          <div className="bg-card border border-border rounded-xl px-3 py-2.5">
            <ExecutionDetails events={events} isStreaming={isStreaming} />
          </div>
        )}
        {reasoning && (
          <div className="bg-card border border-border rounded-xl px-3 py-2.5">
            <ReasoningBlock reasoning={reasoning} isStreaming={isStreaming && !text} />
          </div>
        )}
        {text && (
          <div className="prose prose-sm dark:prose-invert max-w-none text-foreground
            prose-p:leading-relaxed prose-p:my-1
            prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-1
            prose-code:text-[0.8em] prose-code:bg-muted prose-code:px-1 prose-code:py-0.5
            prose-code:rounded prose-code:before:content-none prose-code:after:content-none
            prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-pre:rounded-lg
            prose-pre:text-[0.8em]
            prose-ul:my-1 prose-ol:my-1 prose-li:my-0
            prose-blockquote:border-l-primary/40 prose-blockquote:text-muted-foreground
            prose-hr:border-border prose-a:text-primary prose-strong:font-semibold">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        )}
        {isStreaming && !text && !reasoning && (
          <div className="flex items-center gap-1 pl-1">
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
          </div>
        )}
      </div>
    </div>
  )
}
