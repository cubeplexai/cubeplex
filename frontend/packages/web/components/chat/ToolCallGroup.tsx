'use client'

import { useState, type ReactNode } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  isWriteOrEditTool,
  type AskQuestion,
  type ContentBlock,
  type PendingConfirm,
  type ToolCallRef,
} from '@cubeplex/core'
import { AskUserResolvedCard } from './AskUserResolvedCard'
import { ToolCallItem } from './ToolCallItem'
import { toolResultIsRunning } from './toolResultStatus'
import { getWriteFileSummary } from '@/lib/writeFilePreview'
import { getStreamingParamSummary } from '@/lib/toolIcons'
import { cn } from '@/lib/utils'
import type { ActivityBlock } from '@/lib/foldToolActivity'

type ThinkingBlock = Extract<ContentBlock, { type: 'thinking' }>

interface ToolCallGroupProps {
  blocks: ActivityBlock[]
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number; startedAt?: number; details?: unknown }
  >
  isStreaming: boolean
  /** ISO timestamp of the parent assistant message (used to compute tool call duration) */
  messageCreatedAt?: string
  agentId?: string | null
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
  renderThinking?: (block: ThinkingBlock, opts: { isLast: boolean }) => ReactNode
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
  renderThinking,
}: ToolCallGroupProps) {
  const t = useTranslations('chat')
  // Open only while this group is the part still streaming. A finished
  // group stays collapsed, including after refresh.
  const [userExpanded, setUserExpanded] = useState(false)

  // Resolve children first so we can drop the wrapper entirely if every
  // block renders null (e.g. an ask_user tool_call with no result yet).
  const children: ReactNode[] = blocks.map((block, i) => {
    if (block.type === 'thinking') {
      if (!renderThinking) return null
      return (
        <div key={`thinking-${i}`}>
          {renderThinking(block, { isLast: i === blocks.length - 1 })}
        </div>
      )
    }
    if (block.type === 'tool_call_streaming') {
      if (block.name === 'ask_user') return null
      const supportsPreview = isWriteOrEditTool(block.name)
      const toolCallId = block.tool_call_id ?? `streaming-${i}`
      return (
        <ToolCallItem
          key={toolCallId}
          name={block.name || 'tool'}
          arguments={{}}
          toolCallId={toolCallId}
          summaryOverride={
            supportsPreview && block.name === 'write'
              ? getWriteFileSummary({}, block.args_text)
              : getStreamingParamSummary(block.name || 'tool', block.args_text) || undefined
          }
          contentTypeOverride={supportsPreview ? block.name : undefined}
          toolRef={
            supportsPreview
              ? {
                  agent_id: agentId ?? null,
                  tool_call_id: block.tool_call_id,
                  index: block.index,
                }
              : undefined
          }
          timestamp={messageCreatedAt}
          isPending
          allowOpenWhenPending={supportsPreview}
        />
      )
    }
    const result = toolResultMap[block.id] ?? null
    const isPending = isStreaming && (!result || toolResultIsRunning(result))
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
        contentTypeOverride={isWriteOrEditTool(block.name) ? block.name : undefined}
        toolRef={
          isWriteOrEditTool(block.name)
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
        allowOpenWhenPending={isWriteOrEditTool(block.name)}
        pendingConfirm={pendingConfirmMap?.[block.id] ?? null}
        onSandboxConfirm={onSandboxConfirm ? (d) => onSandboxConfirm(block.id, d) : undefined}
      />
    )
  })

  if (children.every((c) => c === null)) return null

  const visibleCount = blocks.reduce((count, block, index) => {
    if (block.type === 'thinking' || children[index] == null) return count
    return count + 1
  }, 0)
  const hasThinking = blocks.some((block) => block.type === 'thinking')
  const completedCount = blocks.filter(
    (block) =>
      block.type === 'tool_call' &&
      block.name !== 'ask_user' &&
      toolResultMap[block.id] &&
      !toolResultIsRunning(toolResultMap[block.id]),
  ).length
  const pendingCount = isStreaming
    ? blocks.filter((block) => {
        if (block.type === 'thinking' || block.name === 'ask_user') return false
        if (block.type === 'tool_call_streaming') return true
        const result = toolResultMap[block.id]
        return !result || toolResultIsRunning(result)
      }).length
    : 0
  // One bare tool stays a chip. Thinking, or a second tool, makes a fold so
  // the reasoning sits inside the summary instead of between chips.
  const collapsible = visibleCount >= 2 || (visibleCount >= 1 && hasThinking)
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
