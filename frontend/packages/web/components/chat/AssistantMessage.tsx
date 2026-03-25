'use client'

import type { Message, AgentEvent } from '@cubebox/core'
import { ExecutionDetails } from './ExecutionDetails'

function extractFinalText(events: AgentEvent[] | null): string {
  if (!events) return ''
  const lastLlmEnd = [...(events || [])].reverse().find((e) => e.type === 'llm_end')
  return lastLlmEnd?.data?.output ?? ''
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
    <div className="flex justify-start">
      <div className="bg-card border border-border rounded-lg px-4 py-2 max-w-md space-y-2">
        {events && <ExecutionDetails events={events} isStreaming={isStreaming} />}
        {finalText && <div className="text-foreground whitespace-pre-wrap">{finalText}</div>}
        {isStreaming && !finalText && <div className="text-muted-foreground text-sm animate-pulse">生成中...</div>}
      </div>
    </div>
  )
}
