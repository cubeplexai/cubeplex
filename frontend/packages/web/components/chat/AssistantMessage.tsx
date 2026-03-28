'use client'

import type { Message, AgentEvent } from '@cubebox/core'
import { ExecutionDetails } from './ExecutionDetails'
import { Bot } from 'lucide-react'

function extractFinalText(events: AgentEvent[] | null): string {
  if (!events) return ''
  const lastTextDelta = [...events].reverse().find((e) => e.type === 'text_delta')
  return lastTextDelta?.data?.content ?? ''
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
  const finalText = extractFinalText(events)

  return (
    <div className="flex justify-start gap-2.5">
      <div className="shrink-0 w-6 h-6 rounded-md border border-border bg-card flex items-center justify-center mt-0.5">
        <Bot className="size-3.5 text-primary/70" />
      </div>
      <div className="flex-1 max-w-[75%] space-y-2">
        {events && events.length > 0 && (
          <div className="bg-card border border-border rounded-xl px-3 py-2.5">
            <ExecutionDetails events={events} isStreaming={isStreaming} />
          </div>
        )}
        {finalText && (
          <div className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">
            {finalText}
          </div>
        )}
        {isStreaming && !finalText && (
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
