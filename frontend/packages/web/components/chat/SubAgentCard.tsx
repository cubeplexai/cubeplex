'use client'

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ChevronDown,
  ChevronRight,
  Bot,
  CheckCircle2,
} from 'lucide-react'
import type { AgentStream } from '@cubebox/core'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { ToolCallItem } from './ToolCallItem'

interface Props {
  name: string
  stream?: AgentStream
  isRunning: boolean
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
}

const proseClasses = [
  'prose prose-sm dark:prose-invert max-w-none',
  'prose-p:leading-relaxed prose-p:my-1',
  'prose-headings:font-semibold prose-headings:mt-3',
  'prose-headings:mb-1 prose-headings:text-foreground',
  'prose-p:text-foreground prose-li:text-foreground',
  'prose-strong:text-foreground',
  'prose-code:text-foreground prose-code:text-[0.8em]',
  'prose-code:bg-muted prose-code:px-1',
  'prose-code:py-0.5 prose-code:rounded',
  'prose-code:before:content-none',
  'prose-code:after:content-none',
  'prose-pre:bg-muted prose-pre:border',
  'prose-pre:border-border prose-pre:rounded-lg',
  'prose-pre:text-[0.8em]',
  'prose-ul:my-1 prose-ol:my-1 prose-li:my-0',
  'prose-a:text-primary',
].join(' ')

export function SubAgentCard({
  name,
  stream,
  isRunning,
  toolResultMap,
}: Props) {
  const [open, setOpen] = useState(true)

  const hasContent = stream && (
    stream.toolCalls.length > 0 || stream.text
  )

  return (
    <div
      className="border border-border rounded-xl
        overflow-hidden bg-muted/10 border-l-2
        border-l-primary/40"
    >
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-2
            px-3 py-2 text-sm text-muted-foreground
            hover:bg-muted/30 transition-colors"
        >
          {open ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
          <Bot className="size-3.5" />
          <span className="font-medium text-foreground">
            {name}
          </span>
          {isRunning ? (
            <span className="ml-auto flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full
                    bg-muted-foreground animate-pulse"
                  style={{
                    animationDelay: `${i * 200}ms`,
                  }}
                />
              ))}
            </span>
          ) : hasContent ? (
            <CheckCircle2
              className="ml-auto size-3.5
                text-emerald-500"
            />
          ) : null}
        </CollapsibleTrigger>

        <CollapsibleContent>
          {hasContent && (
            <div className="px-1 pb-2 space-y-1">
              {stream.toolCalls.map((tc, i) => {
                const result =
                  toolResultMap[tc.data.tool_call_id]
                  ?? null
                return (
                  <ToolCallItem
                    key={tc.data.tool_call_id || i}
                    name={tc.data.name}
                    arguments={tc.data.arguments}
                    toolCallId={tc.data.tool_call_id}
                    toolResult={result}
                    timestamp={tc.timestamp}
                    isPending={isRunning && !result}
                  />
                )
              })}
              {stream.text && (
                <div
                  className={`px-3 pt-1 ${proseClasses}`}
                >
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                  >
                    {stream.text}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          )}
          {!hasContent && isRunning && (
            <div className="px-3 pb-3 pt-1">
              <span
                className="text-xs text-muted-foreground
                  animate-pulse"
              >
                正在执行...
              </span>
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
