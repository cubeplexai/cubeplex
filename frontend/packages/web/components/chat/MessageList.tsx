'use client'

import { useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { useMessageStore, createApiClient } from '@cubebox/core'
import type { Message, SubagentSummary } from '@cubebox/core'
import { AlertCircle } from 'lucide-react'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { MessageAttachments } from './MessageAttachments'
import { TaskProgressCard } from './TaskProgressCard'
import { TokenUsageBar } from './TokenUsageBar'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'

interface MessageListProps {
  conversationId: string
}

/**
 * Build a map from tool_call_id -> SubagentSummary by scanning tool messages
 * that follow each assistant message.
 */
function buildSubagentDataMap(messages: Message[]): Record<string, SubagentSummary> {
  const map: Record<string, SubagentSummary> = {}
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.name === 'subagent' && msg.tool_call_id && msg.subagent_events) {
      map[`subagent:${msg.tool_call_id}`] = msg.subagent_events
    }
  }
  return map
}

/** Build toolResultMap from historical tool messages so panel works after refresh. */
function buildHistoricalToolResultMap(
  messages: Message[],
): Record<
  string,
  { content: string; receivedAt: number; startedAt?: number; contentType?: string }
> {
  const map: Record<
    string,
    {
      content: string
      receivedAt: number
      startedAt?: number
      contentType?: string
    }
  > = {}
  // Build a map of tool_call_id → authoritative tool start time from history.
  const toolCallStartMap: Record<string, number> = {}
  for (const msg of messages) {
    if (msg.role === 'assistant' && msg.tool_calls) {
      for (const tc of msg.tool_calls) {
        if (!tc.tool_call_id) continue
        const rawStart = tc.started_at ?? msg.created_at
        if (!rawStart) continue
        toolCallStartMap[tc.tool_call_id] = new Date(rawStart).getTime()
      }
    }
  }
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id) {
      const fallbackStartedAt = msg.started_at ? new Date(msg.started_at).getTime() : undefined
      map[msg.tool_call_id] = {
        content: msg.content ?? '',
        receivedAt: new Date(msg.created_at ?? 0).getTime(),
        startedAt: toolCallStartMap[msg.tool_call_id] ?? fallbackStartedAt,
      }
      // Also index subagent inner tool results so their previews/citations work
      if (msg.name === 'subagent' && msg.subagent_events?.tool_results) {
        // Build a started_at map from subagent tool_calls
        const saToolCallStartMap: Record<string, number> = {}
        for (const tc of msg.subagent_events.tool_calls ?? []) {
          if (tc.tool_call_id && tc.started_at) {
            saToolCallStartMap[tc.tool_call_id] = new Date(tc.started_at).getTime()
          }
        }
        const fallbackTs = new Date(msg.created_at ?? 0).getTime()
        for (const tr of msg.subagent_events.tool_results) {
          if (tr.tool_call_id) {
            const startedAt = tr.started_at
              ? new Date(tr.started_at).getTime()
              : (saToolCallStartMap[tr.tool_call_id] ?? undefined)
            map[tr.tool_call_id] = {
              content: tr.content,
              receivedAt: tr.completed_at ? new Date(tr.completed_at).getTime() : fallbackTs,
              startedAt,
              contentType: tr.content_type ?? undefined,
            }
          }
        }
      }
    }
  }
  return map
}

export function MessageList({ conversationId }: MessageListProps) {
  const t = useTranslations('chat')
  const {
    messages,
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    error,
    toolResultMap,
    turnUsage,
    sessionUsage,
    contextWindow,
  } = useMessages(conversationId)
  const loadMessages = useMessageStore((s) => s.loadMessages)
  const lastRunStatus = useMessageStore((s) => s.lastRunStatus)
  const { workspaceId } = useWorkspaceContext()

  useEffect(() => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages, workspaceId])

  const subagentDataMap = useMemo(() => buildSubagentDataMap(messages ?? []), [messages])

  const historicalToolResults = useMemo(
    () => buildHistoricalToolResultMap(messages ?? []),
    [messages],
  )

  // Merge: streaming results take precedence over historical
  const mergedToolResultMap = useMemo(
    () => ({ ...historicalToolResults, ...toolResultMap }),
    [historicalToolResults, toolResultMap],
  )

  // After streaming completes, the assistant message is appended to history
  // while streamAgents is kept intact for smooth transition. Skip the last
  // history assistant message to avoid rendering the same response twice.
  // During active streaming (isStreaming=true), the current turn's assistant
  // message is NOT yet in history, so no dedup is needed — skipping here
  // would incorrectly hide the *previous* turn's response.
  const lastAssistantId = useMemo(() => {
    if (!mainStream || isStreaming) return null
    const msgs = messages ?? []
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') return msgs[i].id
    }
    return null
  }, [messages, mainStream, isStreaming])

  // --- Auto-scroll: keep chat pinned to bottom during streaming ---
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  // Detect whether user has scrolled away from bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const threshold = 80
    stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
  }, [])

  // Use ResizeObserver on inner content — fires whenever content height changes
  // regardless of the cause (new messages, subagent cards appearing, text growing).
  useEffect(() => {
    const content = contentRef.current
    const scroller = scrollRef.current
    if (!content || !scroller) return

    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) {
        scroller.scrollTop = scroller.scrollHeight
      }
    })
    ro.observe(content)
    return () => ro.disconnect()
  }, [])

  // When a new streaming turn starts, force stick to bottom and scroll immediately
  useEffect(() => {
    if (isStreaming) {
      stickToBottom.current = true
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }
    }
  }, [isStreaming])

  return (
    <ScrollArea ref={scrollRef} className="flex-1 p-4" onScroll={handleScroll}>
      <div ref={contentRef} className="space-y-4 max-w-2xl mx-auto">
        {(messages ?? []).map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && (
              <>
                {msg.attachments && msg.attachments.length > 0 && (
                  <MessageAttachments
                    attachments={msg.attachments}
                    conversationId={conversationId}
                  />
                )}
                <UserMessage content={msg.content ?? ''} />
              </>
            )}
            {msg.role === 'assistant' && msg.id !== lastAssistantId && (
              <AssistantMessage
                message={msg}
                subagentDataMap={subagentDataMap}
                toolResultMap={mergedToolResultMap}
                conversationId={conversationId}
              />
            )}
          </div>
        ))}

        {mainStream && (
          <AssistantMessage
            stream={mainStream}
            isStreaming={isStreaming}
            statusPhase={statusPhase}
            subAgentStreams={subAgentStreams}
            toolResultMap={mergedToolResultMap}
            todos={todos}
            conversationId={conversationId}
          />
        )}

        {!isStreaming &&
          (turnUsage || sessionUsage) &&
          (messages ?? []).some((m) => m.role === 'assistant') && (
            <div className="flex justify-start gap-2.5">
              <div className="shrink-0 w-6 h-6" />
              <div className="flex-1 max-w-[75%]">
                <TokenUsageBar
                  turnUsage={turnUsage}
                  sessionUsage={sessionUsage}
                  contextWindow={contextWindow}
                />
              </div>
            </div>
          )}

        {!isStreaming && todos.length > 0 && (
          <div className="flex justify-start gap-2.5">
            <div className="shrink-0 w-6 h-6" />
            <div className="flex-1 max-w-[75%]">
              <TaskProgressCard todos={todos} isStreaming={false} />
            </div>
          </div>
        )}

        {error && (
          <div
            className="flex items-start gap-2 px-3 py-2.5 rounded-lg
            bg-destructive/10 border border-destructive/20 text-destructive text-sm"
          >
            <AlertCircle className="size-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {lastRunStatus === 'stale' && (
          <div
            className="flex items-start gap-2 px-3 py-2.5 rounded-lg
            bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 text-sm"
          >
            <AlertCircle className="size-4 shrink-0 mt-0.5" />
            <span>{t('incompletePreviousAnswer')}</span>
          </div>
        )}
      </div>
    </ScrollArea>
  )
}
