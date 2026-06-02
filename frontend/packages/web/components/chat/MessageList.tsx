'use client'

import { useEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import {
  useMessageStore,
  createApiClient,
  getTextContent,
  getToolResultPreviewContent,
  getSubagentSummary,
  submitSandboxConfirm,
  submitAskUserAnswer,
  ApiError,
} from '@cubebox/core'
import type { Message, SubagentSummary } from '@cubebox/core'
import { AlertCircle } from 'lucide-react'
import { UserMessage } from './UserMessage'
import { AssistantMessage, HistoryAssistantMessage } from './AssistantMessage'
import { AskUserCard } from './AskUserCard'
import { MessageAttachments } from './MessageAttachments'
import { TokenUsageBar } from './TokenUsageBar'
import { MemoryUpdateChip } from './MemoryUpdateChip'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'
import { useMessageScopedToolResults } from '@/hooks/useMessageScopedToolResults'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { rafThrottleScrollToBottom } from '@/lib/scrollToBottom'

interface MessageListProps {
  conversationId: string
}

function msgTimestampMs(msg: Message): number {
  return msg.timestamp != null ? msg.timestamp * 1000 : 0
}

/**
 * Build a map from tool_call_id -> SubagentSummary by scanning tool messages
 * with tool_name === 'subagent'. Subagent summaries ride inside metadata.
 */
function buildSubagentDataMap(messages: Message[]): Record<string, SubagentSummary> {
  const map: Record<string, SubagentSummary> = {}
  for (const msg of messages) {
    if (msg.role !== 'tool_result' || msg.tool_name !== 'subagent' || !msg.tool_call_id) continue
    const summary = getSubagentSummary(msg)
    if (summary) map[`subagent:${msg.tool_call_id}`] = summary
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
  // Build a map of tool_call_id → authoritative tool start time from the
  // assistant message that issued the call (its timestamp is our best proxy).
  const toolCallStartMap: Record<string, number> = {}
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue
    const ts = msgTimestampMs(msg)
    if (!ts) continue
    for (const block of msg.content) {
      if (block.type === 'tool_call') {
        toolCallStartMap[block.id] = ts
      }
    }
  }

  for (const msg of messages) {
    if (msg.role !== 'tool_result' || !msg.tool_call_id) continue
    const receivedAt = msgTimestampMs(msg)
    map[msg.tool_call_id] = {
      content: getToolResultPreviewContent(msg),
      receivedAt: receivedAt || Date.now(),
      startedAt: toolCallStartMap[msg.tool_call_id],
    }
    // Index subagent inner tool results so their previews/citations work
    const summary = msg.tool_name === 'subagent' ? getSubagentSummary(msg) : null
    if (summary?.tool_results) {
      const saToolCallStartMap: Record<string, number> = {}
      for (const tc of summary.tool_calls ?? []) {
        if (tc.id && tc.started_at) {
          saToolCallStartMap[tc.id] = new Date(tc.started_at).getTime()
        }
      }
      for (const tr of summary.tool_results) {
        if (!tr.tool_call_id) continue
        const startedAt = tr.started_at
          ? new Date(tr.started_at).getTime()
          : saToolCallStartMap[tr.tool_call_id]
        map[tr.tool_call_id] = {
          content: tr.content,
          receivedAt: tr.completed_at ? new Date(tr.completed_at).getTime() : receivedAt,
          startedAt,
          contentType: tr.content_type ?? undefined,
        }
      }
    }
  }
  return map
}

/**
 * Map the HITL answer-submit 4xx codes (defined in
 * ``backend/cubebox/api/routes/v1/conversations.py``) to UI recovery actions:
 *
 *   - ``resume_in_flight`` — another submit beat us; reload to pick up the
 *     authoritative state.
 *   - ``stale_answer`` / ``conversation_moved`` — the question we answered is
 *     no longer current; reload to drop the stale card.
 *   - ``no_pending`` (404) — someone else answered (or the run was
 *     cancelled); clear the matching local card without a reload.
 *
 * Any other error is re-thrown so existing callers / error boundaries keep
 * behaving as before.
 */
async function handlePendingSubmitError(
  err: unknown,
  conversationId: string,
  questionId: string,
  workspaceId: string | null | undefined,
  loadMessages: (
    client: ReturnType<typeof createApiClient>,
    conversationId: string,
  ) => Promise<void>,
): Promise<void> {
  if (!(err instanceof ApiError)) throw err
  const code = err.code
  const client = createApiClient('')
  if (workspaceId) client.setWorkspaceId(workspaceId)

  if (
    err.status === 409 &&
    (code === 'resume_in_flight' || code === 'stale_answer' || code === 'conversation_moved')
  ) {
    useMessageStore.setState({ error: err.message })
    await loadMessages(client, conversationId)
    return
  }
  if (err.status === 404 && code === 'no_pending') {
    useMessageStore.setState((s) => {
      const nextConfirm = Object.fromEntries(
        Object.entries(s.pendingConfirmMap).filter(([, v]) => v.question_id !== questionId),
      )
      const nextAsk = s.pendingAsk?.question_id === questionId ? null : s.pendingAsk
      return { pendingConfirmMap: nextConfirm, pendingAsk: nextAsk }
    })
    return
  }
  throw err
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
  const pendingConfirmMap = useMessageStore((s) => s.pendingConfirmMap)
  const pendingAsk = useMessageStore((s) => s.pendingAsk)
  const streamingConversationId = useMessageStore((s) => s.streamingConversationId)
  const { workspaceId } = useWorkspaceContext()

  useEffect(() => {
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages, workspaceId])

  const handleSandboxConfirm = useCallback(
    async (toolCallId: string, decision: 'approve' | 'deny') => {
      const convId = streamingConversationId ?? conversationId
      const pending = pendingConfirmMap[toolCallId]
      if (!pending) return
      const questionId = pending.question_id
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      try {
        await submitSandboxConfirm(client, convId, questionId, decision)
        // Optimistic removal — sandbox_confirm_resolved SSE will also clean up
        useMessageStore.setState((s) => {
          const next = { ...s.pendingConfirmMap }
          delete next[toolCallId]
          return { pendingConfirmMap: next }
        })
        // Reattach to the resumed run stream. The original paused stream
        // ended on the `done` (with paused=true) event; the backend just
        // spawned a fresh respond task on the same run_id and is emitting
        // assistant/tool events that nobody is reading. loadMessages
        // re-bootstraps + tails the new active_run.
        await loadMessages(client, convId)
      } catch (err) {
        await handlePendingSubmitError(err, convId, questionId, workspaceId, loadMessages)
      }
    },
    [conversationId, streamingConversationId, pendingConfirmMap, workspaceId, loadMessages],
  )

  const handleAskUserSubmit = useCallback(
    async (answers: Record<string, string | string[]>) => {
      if (!pendingAsk) return
      const convId = streamingConversationId ?? conversationId
      const questionId = pendingAsk.question_id
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      try {
        await submitAskUserAnswer(client, convId, questionId, answers)
        // Optimistic clear — ask_user_resolved SSE will also clean up
        useMessageStore.setState({ pendingAsk: null })
        // Reattach to the resumed run stream — same reason as above.
        await loadMessages(client, convId)
      } catch (err) {
        await handlePendingSubmitError(err, convId, questionId, workspaceId, loadMessages)
      }
    },
    [conversationId, streamingConversationId, pendingAsk, workspaceId, loadMessages],
  )

  const subagentDataMap = useMemo(() => buildSubagentDataMap(messages ?? []), [messages])

  const historicalToolResults = useMemo(
    () => buildHistoricalToolResultMap(messages ?? []),
    [messages],
  )

  // Merge: streaming results take precedence over historical. Used for the
  // live streaming bubble; history bubbles use per-message scoped subsets
  // (`messageScopedToolResults`) so memo bails out on most streaming ticks.
  const mergedToolResultMap = useMemo(
    () => ({ ...historicalToolResults, ...toolResultMap }),
    [historicalToolResults, toolResultMap],
  )

  // Per-message subset of (live ?? historical) tool results, keyed by message
  // id. Each per-message subset keeps its reference across renders unless one
  // of that message's tool_call_ids gained or changed an entry — so a
  // `tool_result` for tool_call X only re-renders the historical bubble that
  // actually carries X, leaving every other history message memo'd. Fixes the
  // "committed bubble loses its result until next finalize" case codex P2
  // flagged on PR #188.
  const messageScopedToolResults = useMessageScopedToolResults(
    messages ?? [],
    historicalToolResults,
    toolResultMap,
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

    // Re-check stickToBottom inside the rAF too: a user scrolling up between
    // the ResizeObserver fire and the next frame would otherwise be yanked
    // back to the bottom by a stale decision.
    const scheduleScroll = rafThrottleScrollToBottom(
      () => scrollRef.current,
      () => stickToBottom.current,
    )
    const ro = new ResizeObserver(() => {
      if (stickToBottom.current) scheduleScroll()
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
                {msg.metadata?.attachments && msg.metadata.attachments.length > 0 && (
                  <MessageAttachments
                    attachments={msg.metadata.attachments}
                    conversationId={conversationId}
                  />
                )}
                <UserMessage content={getTextContent(msg)} />
              </>
            )}
            {msg.role === 'assistant' && msg.id !== lastAssistantId && (
              <HistoryAssistantMessage
                message={msg}
                subagentDataMap={subagentDataMap}
                toolResultMap={messageScopedToolResults[msg.id] ?? historicalToolResults}
                conversationId={conversationId}
                pendingConfirmMap={pendingConfirmMap}
                onSandboxConfirm={handleSandboxConfirm}
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
            pendingConfirmMap={pendingConfirmMap}
            onSandboxConfirm={handleSandboxConfirm}
          />
        )}

        {pendingAsk && streamingConversationId === conversationId && (
          <div className="flex gap-2.5">
            <div className="shrink-0 w-6 h-6" />
            <div className="flex-1 max-w-[75%]">
              <AskUserCard
                key={pendingAsk.question_id}
                pending={pendingAsk}
                onSubmit={handleAskUserSubmit}
              />
            </div>
          </div>
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

        <MemoryUpdateChip conversationId={conversationId} />
      </div>
    </ScrollArea>
  )
}
