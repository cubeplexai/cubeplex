'use client'

import { useState } from 'react'
import type { AgentEvent } from '@cubebox/core'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

interface ExecutionDetailsProps {
  events: AgentEvent[]
  isStreaming?: boolean
}

function getEventDisplay(event: AgentEvent) {
  switch (event.type) {
    case 'chain_start':
      return { icon: '🚀', label: '开始执行' }
    case 'llm_start':
      return { icon: '🧠', label: '思考中...' }
    case 'llm_end':
      return { icon: '✓', label: '生成完成' }
    case 'tool_start':
      return {
        icon: '⚙',
        label: `${event.data?.tool_name || '工具'} · 输入: ${JSON.stringify(event.data?.input).slice(0, 50)}...`,
      }
    case 'tool_end':
      return {
        icon: '✓',
        label: `结果: ${JSON.stringify(event.data?.output).slice(0, 50)}...`,
      }
    case 'chain_end':
      return { icon: '✓', label: '完成' }
    case 'error':
      return {
        icon: '✗',
        label: `错误: ${event.data?.message || 'Unknown error'}`,
      }
    default:
      return { icon: '•', label: event.type }
  }
}

function summarizeEvents(events: AgentEvent[]): string {
  const toolCount = events.filter((e) => e.type === 'tool_start').length
  const duration = events.length > 0
    ? new Date(events[events.length - 1].timestamp).getTime() - new Date(events[0].timestamp).getTime()
    : 0
  return `已完成 · ${toolCount} 个工具调用 · ${(duration / 1000).toFixed(1)}s`
}

export function ExecutionDetails({ events, isStreaming = false }: ExecutionDetailsProps) {
  const [isOpen, setIsOpen] = useState(isStreaming)
  const displayEvents = events.filter((e) => e.type !== 'done')

  if (displayEvents.length === 0) return null

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="text-xs text-muted-foreground hover:text-foreground transition-colors">
        {isOpen ? '▼' : '▶'} {summarizeEvents(displayEvents)}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2 text-xs">
        {displayEvents.map((event, idx) => {
          const { icon, label } = getEventDisplay(event)
          return (
            <div key={idx} className="flex items-center gap-2">
              <span>{icon}</span>
              <span className="text-muted-foreground">{label}</span>
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
