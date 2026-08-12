'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { AskQuestion, ContentBlock, PendingConfirm, ToolCallRef } from '@cubeplex/core'
import { AskUserResolvedCard } from './AskUserResolvedCard'
import { ToolCallItem } from './ToolCallItem'
import { cn } from '@/lib/utils'

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

/**
 * Beautiful-UI-style tool chip list: no heavy card chrome.
 * Multi-tool groups collapse to a single summary line; expand shows compact chips.
 * Clicking a chip still opens the right-hand tool detail panel.
 */
export function ToolCallGroup({
  blocks,
  toolResultMap,
  isStreaming,
  messageCreatedAt,
  agentId,
  pendingConfirmMap,
  onSandboxConfirm,
}: ToolCallGroupProps) {
  const t = useTranslations('chat')
  // Multi-tool: open while streaming; after settle, collapsed by default.
  const [userExpanded, setUserExpanded] = useState(false)

  // Resolve children first so we can drop the wrapper entirely if every
  // block renders null (e.g. an ask_user tool_call with no result yet).
  const children: ReactNode[] = blocks.map((block, i) => {
    const result = toolResultMap[block.id] ?? null
    const isPending = isStreaming && !result
    if (block.name === 'ask_user' && result) {
      const questions = extractAskQuestions(block.arguments)
      if (questions && questions.length > 0) {
        return (
          <div key={block.id || i}>
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
        pendingConfirm={pendingConfirmMap?.[block.id] ?? null}
        onSandboxConfirm={onSandboxConfirm ? (d) => onSandboxConfirm(block.id, d) : undefined}
      />
    )
  })

  if (children.every((c) => c === null)) return null

  const visibleCount = children.filter((c) => c != null).length
  const completedCount = blocks.filter((b) => b.name !== 'ask_user' && toolResultMap[b.id]).length
  const pendingCount = blocks.filter(
    (b) => b.name !== 'ask_user' && isStreaming && !toolResultMap[b.id],
  ).length
  const collapsible = visibleCount >= 2
  const isExpanded = isStreaming || userExpanded
  const showList = !collapsible || isExpanded

  return (
    <div className="min-w-0 max-w-full">
      {collapsible && (
        <button
          type="button"
          onClick={() => {
            if (isStreaming) return
            setUserExpanded((v) => !v)
          }}
          aria-expanded={isExpanded}
          className={cn(
            '-mx-1.5 flex w-fit max-w-full items-center gap-1 rounded-md px-1.5 py-0.5',
            'text-[12.5px] leading-5 text-muted-foreground transition-colors',
            isStreaming
              ? 'cursor-default'
              : 'cursor-pointer hover:bg-muted/70 hover:text-foreground',
          )}
        >
          {isExpanded ? (
            <ChevronDown className="size-3 shrink-0 opacity-70" />
          ) : (
            <ChevronRight className="size-3 shrink-0 opacity-70" />
          )}
          <span className="tabular-nums">
            {t('toolCallsSummary', { count: visibleCount })}
            {pendingCount > 0
              ? ` · ${t('toolCallsRunning', { completed: completedCount, total: visibleCount })}`
              : completedCount > 0
                ? ` · ${t('toolCallsDone', { count: completedCount })}`
                : ''}
          </span>
        </button>
      )}
      {showList && (
        <div className={cn('flex min-w-0 flex-col gap-px', collapsible && 'mt-0.5')}>
          {children}
        </div>
      )}
    </div>
  )
}
