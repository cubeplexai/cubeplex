'use client'

import { useState } from 'react'
import type { AgentEvent } from '@cubebox/core'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ChevronRight, ChevronDown, Clock, Wrench, CheckCircle2, Loader2 } from 'lucide-react'

interface ExecutionDetailsProps {
  events: AgentEvent[]
  isStreaming?: boolean
}

type EventMeta = { label: string; muted: boolean }

function getEventMeta(type: string, data?: Record<string, unknown>): EventMeta {
  switch (type) {
    case 'chain_start': return { label: '链启动', muted: true }
    case 'text_delta':  return { label: '文本生成', muted: false }
    case 'reasoning':   return { label: '推理中', muted: false }
    case 'tool_call':   return { label: `工具: ${data?.name ?? '调用'}`, muted: false }
    case 'tool_result': return { label: '工具完成', muted: true }
    case 'error':       return { label: `错误`, muted: false }
    default:            return { label: type, muted: true }
  }
}

function getEventDetail(event: AgentEvent): string | null {
  switch (event.type) {
    case 'tool_call':
      return event.data?.arguments ? JSON.stringify(event.data.arguments).slice(0, 80) : null
    case 'tool_result':
      return event.data?.content ? JSON.stringify(event.data.content).slice(0, 80) : null
    case 'error':
      return event.data?.message ?? null
    default:
      return null
  }
}

function summarize(events: AgentEvent[]): { tools: number; durationMs: number } {
  const tools = events.filter((e) => e.type === 'tool_call').length
  const durationMs =
    events.length > 1
      ? new Date(events[events.length - 1].timestamp).getTime() -
        new Date(events[0].timestamp).getTime()
      : 0
  return { tools, durationMs }
}

export function ExecutionDetails({ events, isStreaming = false }: ExecutionDetailsProps) {
  // Only show tool calls, tool results, and errors — filter out text/reasoning noise
  const displayEvents = events.filter(
    (e) => e.type === 'tool_call' || e.type === 'tool_result' || e.type === 'error'
  )
  const hasTools = displayEvents.some((e) => e.type === 'tool_call')
  const [isOpen, setIsOpen] = useState(hasTools || isStreaming)

  if (displayEvents.length === 0 && !isStreaming) return null

  const { tools, durationMs } = summarize(events.filter((e) => e.type !== 'done'))

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors w-full group">
        <span className="text-muted-foreground/60 group-hover:text-muted-foreground transition-colors">
          {isOpen ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>
        {isStreaming ? (
          <Loader2 className="size-3 text-primary animate-spin" />
        ) : (
          <CheckCircle2 className="size-3 text-primary" />
        )}
        <span className={isStreaming ? 'text-primary/80' : 'text-primary/70'}>
          {isStreaming ? '执行中' : '已完成'}
        </span>
        {tools > 0 && (
          <span className="flex items-center gap-1 text-muted-foreground/70">
            <Wrench className="size-2.5" />
            {tools} 工具
          </span>
        )}
        {durationMs > 0 && (
          <span className="flex items-center gap-1 text-muted-foreground/70">
            <Clock className="size-2.5" />
            {(durationMs / 1000).toFixed(1)}s
          </span>
        )}
      </CollapsibleTrigger>

      <CollapsibleContent className="mt-2">
        <div className="space-y-1 pl-4 border-l border-border/60">
          {displayEvents.length === 0 && isStreaming && (
            <div className="text-[11px] text-muted-foreground/50 py-0.5">处理中...</div>
          )}
          {displayEvents.map((event, idx) => {
            const meta = getEventMeta(event.type, event.data as Record<string, unknown> | undefined)
            const detail = getEventDetail(event)
            const isError = event.type === 'error'
            return (
              <div key={idx} className="flex items-start gap-2 text-[11px] py-0.5">
                <span
                  className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    isError
                      ? 'text-red-400 bg-red-400/10'
                      : meta.muted
                      ? 'text-muted-foreground/60 bg-muted/30'
                      : 'text-primary/80 bg-primary/8'
                  }`}
                >
                  {meta.label}
                </span>
                {detail && (
                  <span className="text-muted-foreground/50 truncate font-mono text-[10px] mt-0.5">
                    {detail}
                  </span>
                )}
              </div>
            )
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
