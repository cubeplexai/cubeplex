'use client'

import type { ReactNode } from 'react'
import type { AskQuestion, ContentBlock, PendingConfirm, ToolCallRef } from '@cubeplex/core'
import { AskUserResolvedCard } from './AskUserResolvedCard'
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

function extractAskQuestions(args: Record<string, unknown>): AskQuestion[] | null {
  const raw = args?.questions
  if (!Array.isArray(raw)) return null
  // The args shape mirrors AskQuestion; we trust the agent emitted it correctly.
  return raw as AskQuestion[]
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
  // Resolve children first so we can drop the wrapper entirely if every
  // block renders null (e.g. an ask_user tool_call with no result yet —
  // an empty bordered card looks like a stray horizontal line above the
  // live form).
  const children: ReactNode[] = blocks.map((block, i) => {
    const result = toolResultMap[block.id] ?? null
    const isPending = isStreaming && !result
    if (block.name === 'ask_user' && result) {
      const questions = extractAskQuestions(block.arguments)
      if (questions && questions.length > 0) {
        return (
          <div key={block.id || i} className={i > 0 ? 'border-t border-border' : undefined}>
            <AskUserResolvedCard questions={questions} resultContent={result.content} />
          </div>
        )
      }
    }
    // Suppress an ask_user tool_call without a tool_result. The live
    // <AskUserCard> renders the question separately; the generic tool
    // widget would dump raw JSON.
    if (block.name === 'ask_user') return null
    return (
      <ToolCallItem
        key={block.id || i}
        name={block.name}
        arguments={block.arguments}
        toolCallId={block.id}
        contentTypeOverride={
          block.name === 'write_file' || block.name === 'edit_file' ? block.name : undefined
        }
        toolRef={
          block.name === 'write_file' || block.name === 'edit_file'
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
        allowOpenWhenPending={block.name === 'write_file' || block.name === 'edit_file'}
        showDivider={i > 0}
        pendingConfirm={pendingConfirmMap?.[block.id] ?? null}
        onSandboxConfirm={onSandboxConfirm ? (d) => onSandboxConfirm(block.id, d) : undefined}
      />
    )
  })

  if (children.every((c) => c === null)) return null

  return (
    <div className="border border-border rounded bg-card divide-y divide-border">{children}</div>
  )
}
