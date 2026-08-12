'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight, Wrench } from 'lucide-react'
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
  // Multi-tool groups: always open while streaming; after settle, default
  // collapsed (Beautiful UI tool chips). User can re-expand without an effect.
  const [userExpanded, setUserExpanded] = useState(false)

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
          <div key={block.id || i} className={i > 0 ? 'border-t border-border/70' : undefined}>
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

  const visibleCount = children.filter((c) => c != null).length
  const completedCount = blocks.filter((b) => toolResultMap[b.id]).length
  const pendingCount = blocks.filter(
    (b) => b.name !== 'ask_user' && isStreaming && !toolResultMap[b.id],
  ).length
  const collapsible = visibleCount >= 2
  // Streaming always reveals rows; after settle, collapsed unless user opens.
  const isExpanded = isStreaming || userExpanded
  const showList = !collapsible || isExpanded

  return (
    <div
      className={cn(
        'overflow-hidden rounded-xl border border-border/80 bg-card shadow-sm',
        pendingCount > 0 && 'border-l-2 border-l-info-fg/40',
        pendingCount === 0 && completedCount > 0 && 'border-l-2 border-l-success-fg/30',
      )}
    >
      {collapsible && (
        <button
          type="button"
          onClick={() => {
            if (isStreaming) return
            setUserExpanded((v) => !v)
          }}
          aria-expanded={isExpanded}
          className={cn(
            'flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs',
            'text-muted-foreground transition-colors',
            isStreaming
              ? 'cursor-default'
              : 'cursor-pointer hover:bg-accent/60 hover:text-foreground',
          )}
        >
          {isExpanded ? (
            <ChevronDown className="size-3 shrink-0" />
          ) : (
            <ChevronRight className="size-3 shrink-0" />
          )}
          <Wrench className="size-3 shrink-0 text-muted-foreground/70" />
          <span className="font-medium tabular-nums">
            {t('toolCallsSummary', { count: visibleCount })}
          </span>
          {pendingCount > 0 ? (
            <span className="ml-auto tabular-nums text-muted-foreground/60">
              {t('toolCallsRunning', { completed: completedCount, total: visibleCount })}
            </span>
          ) : (
            <span className="ml-auto tabular-nums text-muted-foreground/50">
              {t('toolCallsDone', { count: completedCount })}
            </span>
          )}
        </button>
      )}
      {showList && <div className={cn(collapsible && 'border-t border-border/70')}>{children}</div>}
    </div>
  )
}
