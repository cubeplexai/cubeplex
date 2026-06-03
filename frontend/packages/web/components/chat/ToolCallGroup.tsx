'use client'

import type { AskQuestion, ContentBlock, PendingConfirm, ToolCallRef } from '@cubebox/core'
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
  return (
    <div
      className="bg-card border border-border rounded-xl
        overflow-hidden border-l-2
        border-l-muted-foreground/20"
    >
      {blocks.map((block, i) => {
        const result = toolResultMap[block.id] ?? null
        const isPending = isStreaming && !result
        // ask_user has no question/answer body unless we render it from
        // the tool_call args + matching tool_result. Render the Q/A
        // card only when the tool_result has arrived; while the request
        // is still pending the standalone <AskUserCard> (driven by
        // pendingAsk) is already showing the question, so rendering it
        // here too would duplicate it.
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
        // Fully suppress an ask_user tool_call that has no result and no
        // live <AskUserCard> would not fire (the message reached us via
        // history but the result is still in flight). The generic tool
        // widget renders raw JSON which is the original complaint.
        if (block.name === 'ask_user') {
          return null
        }
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
