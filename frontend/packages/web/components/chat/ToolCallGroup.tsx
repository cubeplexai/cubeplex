'use client'

import type { ContentBlock, PendingConfirm, ToolCallRef } from '@cubebox/core'
import { ToolCallItem } from './ToolCallItem'

interface ToolCallGroupProps {
  blocks: (ContentBlock & { type: 'tool_call' })[]
  toolResultMap: Record<string, { content: string; receivedAt: number; startedAt?: number }>
  isStreaming: boolean
  /** ISO timestamp of the parent assistant message (used to compute tool call duration) */
  messageCreatedAt?: string
  agentId?: string | null
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
}

export function ToolCallGroup({
  blocks,
  toolResultMap,
  isStreaming,
  messageCreatedAt,
  agentId,
  pendingConfirmMap,
  onSandboxConfirm,
}: ToolCallGroupProps) {
  return (
    <div
      className="bg-card border border-border rounded-xl
        overflow-hidden border-l-2
        border-l-muted-foreground/20"
    >
      {blocks.map((block, i) => {
        const result = toolResultMap[block.id] ?? null
        const isPending = isStreaming && !result
        return (
          <ToolCallItem
            key={block.id || i}
            name={block.name}
            arguments={block.arguments}
            toolCallId={block.id}
            contentTypeOverride={block.name === 'write_file' ? 'write_file' : undefined}
            toolRef={
              block.name === 'write_file'
                ? ({
                    agent_id: agentId ?? null,
                    tool_call_id: block.id,
                    index: null,
                  } satisfies ToolCallRef)
                : undefined
            }
            toolResult={result}
            timestamp={messageCreatedAt}
            isPending={isPending}
            allowOpenWhenPending={block.name === 'write_file'}
            showDivider={i > 0}
            pendingConfirm={pendingConfirmMap?.[block.id] ?? null}
            onSandboxConfirm={onSandboxConfirm ? (d) => onSandboxConfirm(block.id, d) : undefined}
          />
        )
      })}
    </div>
  )
}
