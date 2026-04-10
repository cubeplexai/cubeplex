'use client'

import type { ContentBlock, ToolCallRef } from '@cubebox/core'
import { ToolCallItem } from './ToolCallItem'

interface ToolCallGroupProps {
  blocks: (ContentBlock & { type: 'tool_call' })[]
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number; startedAt?: number }
  >
  isStreaming: boolean
  /** ISO timestamp of the parent assistant message (used to compute tool call duration) */
  messageCreatedAt?: string
  agentId?: string | null
}

export function ToolCallGroup({
  blocks,
  toolResultMap,
  isStreaming,
  messageCreatedAt,
  agentId,
}: ToolCallGroupProps) {
  return (
    <div
      className="bg-card border border-border rounded-xl
        overflow-hidden border-l-2
        border-l-muted-foreground/20"
    >
      {blocks.map((block, i) => {
        const result =
          toolResultMap[block.tool_call_id] ?? null
        const isPending = isStreaming && !result
        return (
          <ToolCallItem
            key={block.tool_call_id || i}
            name={block.name}
            arguments={block.arguments}
            toolCallId={block.tool_call_id}
            contentTypeOverride={block.name === 'write_file' ? 'write_file' : undefined}
            toolRef={block.name === 'write_file'
              ? {
                  agent_id: agentId ?? null,
                  tool_call_id: block.tool_call_id,
                  index: null,
                } satisfies ToolCallRef
              : undefined}
            toolResult={result}
            timestamp={messageCreatedAt}
            isPending={isPending}
            allowOpenWhenPending={block.name === 'write_file'}
            showDivider={i > 0}
          />
        )
      })}
    </div>
  )
}
