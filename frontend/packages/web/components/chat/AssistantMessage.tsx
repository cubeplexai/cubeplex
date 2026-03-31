'use client'

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '@cubebox/core'
import type { AgentStream } from '@cubebox/core'
import { Bot, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

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
}

interface StreamingProps {
  message?: never
  stream: AgentStream
  isStreaming: true
}

type AssistantMessageProps = HistoryProps | StreamingProps

export function AssistantMessage({ message, stream, isStreaming }: AssistantMessageProps) {
  const text = isStreaming ? stream.text : (message.content ?? '')
  const reasoning = isStreaming ? stream.reasoning : (message.reasoning ?? '')
  const toolCalls = isStreaming
    ? stream.toolCalls.map((tc) => ({ name: tc.data.name, arguments: tc.data.arguments }))
    : (message.tool_calls ?? [])

  return (
    <div data-role="assistant" className="flex justify-start gap-2.5">
      <div className="shrink-0 w-6 h-6 rounded-md border border-border bg-card flex items-center justify-center mt-0.5">
        <Bot className="size-3.5 text-primary/70" />
      </div>
      <div className="flex-1 max-w-[75%] space-y-2">
        {toolCalls.length > 0 && (
          <div className="bg-card border border-border rounded-xl px-3 py-2.5">
            <ToolCallList toolCalls={toolCalls} />
          </div>
        )}
        {reasoning && (
          <div className="bg-card border border-border rounded-xl px-3 py-2.5">
            <ReasoningBlock reasoning={reasoning} isStreaming={isStreaming === true && !text} />
          </div>
        )}
        {text ? (
          <div className="prose prose-sm dark:prose-invert max-w-none
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
            prose-table:text-foreground prose-th:text-foreground prose-td:text-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        ) : isStreaming ? (
          <div className="flex items-center gap-1 pl-1">
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
          </div>
        ) : null}
      </div>
    </div>
  )
}
