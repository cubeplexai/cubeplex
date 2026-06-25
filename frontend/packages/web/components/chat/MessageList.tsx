'use client'

import { useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react'
import { useTranslations } from 'next-intl'
import {
  useMessageStore,
  useConversationStore,
  createApiClient,
  getTextContent,
  getToolResultPreviewContent,
  getSubagentSummary,
  submitSandboxConfirm,
  submitAskUserAnswer,
  cancelActiveRun,
  ApiError,
} from '@cubebox/core'
import type {
  AssistantMessage as AssistantMessageType,
  Message,
  SubagentSummary,
  TurnUsage,
} from '@cubebox/core'
import { AlertCircle } from 'lucide-react'
import { RunErrorBubble } from './RunErrorBubble'
import { UserMessage } from './UserMessage'
import { SenderBadge } from './SenderBadge'
import { AssistantMessage, HistoryAssistantMessage } from './AssistantMessage'
import { AskUserCard } from './AskUserCard'
import { FailoverBanner } from './FailoverBanner'
import { MessageAttachments } from './MessageAttachments'
import type { FailoverEvent } from '@/lib/types/events'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'
import { useMessageScopedToolResults } from '@/hooks/useMessageScopedToolResults'
import { useWorkspaceContext } from '@/hooks/useWorkspaceContext'
import { rafThrottleScrollToBottom } from '@/lib/scrollToBottom'

interface MessageListProps {
  conversationId: string
}

// Module-level stable empty array — avoids breaking Zustand's `===` selector
// equality when the failover slice is missing/empty.
const EMPTY_FAILOVER_EVENTS: FailoverEvent[] = []

function msgTimestampMs(msg: Message): number {
  return msg.timestamp != null ? msg.timestamp * 1000 : 0
}

// Assemble a clean copy-text snapshot of one assistant message. Models often
// emit text blocks around tool_use boundaries with trailing newlines, and
// ``getTextContent``'s zero-separator join would pile them up — yielding
// stretches of blank lines that show up in the user's clipboard. Trim each
// text block individually, then rejoin with a single paragraph break.
function assistantMessageCopyText(msg: AssistantMessageType): string {
  return msg.content
    .filter((b): b is Extract<typeof b, { type: 'text' }> => b.type === 'text')
    .map((b) => b.text.trim())
    .filter((s) => s.length > 0)
    .join('\n\n')
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
    useMessageStore.setState((s) => ({
      errors: {
        ...s.errors,
        [conversationId]: {
          runId: s.currentRunId ?? '',
          data: { error_code: 'internal_error', message: err.message },
        },
      },
    }))
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
    conversationError,
    toolResultMap,
    sessionUsage,
    contextWindow,
    contextTokens,
  } = useMessages(conversationId)
  const loadMessages = useMessageStore((s) => s.loadMessages)
  const loadOlderMessages = useMessageStore((s) => s.loadOlderMessages)
  const loadOlderUntilSeq = useMessageStore((s) => s.loadOlderUntilSeq)
  const hasMoreOlder = useMessageStore((s) => s.hasMoreByConv[conversationId] ?? false)
  const isLoadingOlder = useMessageStore((s) => s.loadingOlderByConv[conversationId] ?? false)
  const oldestSeq = useMessageStore((s) => s.oldestSeqByConv[conversationId] ?? null)
  const lastRunStatus = useMessageStore((s) => s.lastRunStatus)
  const pendingConfirmMap = useMessageStore((s) => s.pendingConfirmMap)
  const pendingAsk = useMessageStore((s) => s.pendingAsk)
  const streamingConversationId = useMessageStore((s) => s.streamingConversationId)
  // Active run guard for the per-message Fork action — MessageActions
  // greys the button when the clicked message is part of the still-running
  // turn (cubepi rejects ``cp.fork`` with run_not_completed until the
  // run's ``completion_seq`` is stamped).
  const activeRunId = useMessageStore((s) => s.currentRunId)
  // `failoverEvents` is populated by the message store's SSE consumer
  // whenever a `model_failover` event arrives. The core slice's per-event
  // shape is structurally identical to the web `FailoverEvent` type, so
  // the renderer below works against either; the cast bridges only the
  // local-type vs core-type identity gap, not a missing slice.
  const failoverEvents = useMessageStore(
    (s) =>
      (s.failoverEvents[conversationId] as FailoverEvent[] | undefined) ?? EMPTY_FAILOVER_EVENTS,
  )
  const { workspaceId } = useWorkspaceContext()
  // Fork action needs to know whether this is a group chat (the backend
  // rejects forks on group chats, and we render the disabled-state UI for
  // it). Conversation row is loaded by the parent page; if it's not in the
  // store yet (rare race), assume false — a fork attempt will still get a
  // graceful 400 toast.
  const isGroupChat = useConversationStore(
    (s) => s.conversations.find((c) => c.id === conversationId)?.is_group_chat ?? false,
  )

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
        // Optimistic removal — sandbox_confirm_resolved SSE will also clean up.
        // `lastResolvedSandboxQuestionId` tells the subsequent bootstrap
        // not to re-seed this exact confirm while the backend's
        // `save_pending_request(None)` is in flight.
        useMessageStore.setState((s) => {
          const next = { ...s.pendingConfirmMap }
          delete next[toolCallId]
          return { pendingConfirmMap: next, lastResolvedSandboxQuestionId: questionId }
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
        // Keep the form mounted (still in "submitting" state) until
        // either the SSE `ask_user_resolved` event clears `pendingAsk`
        // OR the bootstrap reload below brings back a state without
        // this question pending. The optimistic clear we did before
        // produced a visible blank gap while the resolved card data
        // arrived; preserving the form bridges it smoothly.
        //
        // `lastAnsweredAskQuestionId` tells the subsequent bootstrap
        // not to re-seed this exact ask while the backend's
        // `save_pending_request(None)` is in flight. The companion
        // change in loadConversation preserves the existing
        // `pendingAsk` instead of clearing it when this flag matches.
        useMessageStore.setState({ lastAnsweredAskQuestionId: questionId })
        // Reattach to the resumed run stream — same reason as above.
        await loadMessages(client, convId)
      } catch (err) {
        await handlePendingSubmitError(err, convId, questionId, workspaceId, loadMessages)
      }
    },
    [conversationId, streamingConversationId, pendingAsk, workspaceId, loadMessages],
  )

  const handleAskUserCancel = useCallback(async () => {
    if (!pendingAsk) return
    const convId = streamingConversationId ?? conversationId
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    // Optimistic clear so the form disappears immediately. The backend
    // /cancel route is paused-aware (POST hits cancel_paused_run when
    // status=paused_hitl), which writes a synthetic AgentAbortedEvent
    // and finalises the run as cancelled. Reload bootstrap to pick up
    // the new terminal state — composer unlocks once pendingAsk clears.
    // Mirror the submit handler: keep the form mounted (it will switch
    // to its "cancelling" state via the AskUserCard button) until the
    // backend's cancel-respond flow finishes and SSE clears pendingAsk.
    // `lastAnsweredAskQuestionId` is the bootstrap-guard.
    useMessageStore.setState({ lastAnsweredAskQuestionId: pendingAsk.question_id })
    try {
      await cancelActiveRun(client, convId)
    } catch (err) {
      // Cancel failed — drop the bootstrap-guard so the bootstrap below
      // can re-seed the form. The form was never unmounted, just held
      // in its "cancelling" state; clearing the guard lets the next
      // bootstrap rewrite `pendingAsk` from `pending_hitl`.
      useMessageStore.setState({ lastAnsweredAskQuestionId: null })
      console.error('Failed to cancel paused ask_user:', err)
    } finally {
      await loadMessages(client, convId)
    }
  }, [conversationId, streamingConversationId, pendingAsk, workspaceId, loadMessages])

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

  // `finalizeCompletedStream` / `finalizePausedStream` reset `streamAgents`
  // atomically with the new message append, so mainStream is null at the
  // exact tick the new history entry becomes visible — nothing to dedup.
  // The previous "skip the last history assistant" heuristic over-fired
  // during a resume turn, where mainStream holds the NEW assistant but
  // the last history assistant is still the PREVIOUS (pause-turn) one;
  // skipping it briefly hid the Thinking + Q/A card. Always render history.
  const lastAssistantId: string | null = null

  // Per-run action row anchors: per cubepi semantics fork is run-granular
  // (``cp.fork`` takes ``after_run_id``, copies the whole run). A run may
  // produce multiple assistant bubbles (thinking → tool_use → final text)
  // all sharing one ``run_id`` — clicking fork on any of them would
  // produce the same result. Pin the per-turn action row (token chip,
  // fork button, and — for the latest run — the memory chip) to the
  // *last* assistant bubble of each run so the affordances match the
  // fork point 1:1.
  const { anchorByMessageId, lastAnchorMessageId } = useMemo(() => {
    const lastIdByRun = new Map<string, string>()
    const usageByRun = new Map<string, TurnUsage>()
    // Aggregate the run's user-visible text across every assistant message
    // sharing the run_id (a multi-step turn can emit text mid-run as well
    // as the final answer). Copy = "this turn's reply", not just the tail
    // bubble's text. Thinking and tool_use blocks are skipped by
    // ``assistantMessageCopyText``, which also trims each text block so
    // tool-call boundaries don't pile up blank lines.
    const textByRun = new Map<string, string>()
    let tailRunId: string | null = null
    for (const msg of messages ?? []) {
      if (msg.role !== 'assistant' || !msg.run_id) continue
      lastIdByRun.set(msg.run_id, msg.id)
      tailRunId = msg.run_id
      const text = assistantMessageCopyText(msg)
      if (text) {
        const prev = textByRun.get(msg.run_id)
        textByRun.set(msg.run_id, prev ? `${prev}\n\n${text}` : text)
      }
      const u = msg.usage
      if (!u) continue
      const acc = usageByRun.get(msg.run_id) ?? {
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
      }
      acc.input_tokens += u.input_tokens
      acc.output_tokens += u.output_tokens
      acc.cache_read_tokens += u.cache_read_tokens ?? 0
      acc.cache_write_tokens += u.cache_write_tokens ?? 0
      usageByRun.set(msg.run_id, acc)
    }
    const byMessageId = new Map<
      string,
      { runId: string; turnUsage: TurnUsage | null; copyText: string }
    >()
    for (const [runId, msgId] of lastIdByRun) {
      byMessageId.set(msgId, {
        runId,
        turnUsage: usageByRun.get(runId) ?? null,
        copyText: textByRun.get(runId) ?? '',
      })
    }
    return {
      anchorByMessageId: byMessageId,
      lastAnchorMessageId: tailRunId ? (lastIdByRun.get(tailRunId) ?? null) : null,
    }
  }, [messages])

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

  // Scroll-anchor snapshot taken right before a backscroll request. After the
  // older slice renders we shift scrollTop by exactly (newHeight - oldHeight),
  // so the message that was at the top of the viewport stays at the top
  // instead of jumping when content is prepended.
  const pendingAnchorRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null)

  const handleLoadEarlier = useCallback(() => {
    const scroller = scrollRef.current
    if (scroller) {
      pendingAnchorRef.current = {
        scrollHeight: scroller.scrollHeight,
        scrollTop: scroller.scrollTop,
      }
      // Suppress the streaming-stick-to-bottom auto-scroll for this update —
      // the user is reading old history, they don't want to be yanked back
      // to the live tail.
      stickToBottom.current = false
    }
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    void loadOlderMessages(client, conversationId)
  }, [conversationId, loadOlderMessages, workspaceId])

  // ``oldestSeq`` moves only when older messages are prepended (bootstrap
  // arrival or backscroll); appended turns / mid-stream finalizes don't
  // touch it. Keying the anchor restore on that signal — instead of
  // ``messages.length`` — avoids consuming the snapshot on an unrelated
  // append that happens to land between Load earlier's click and its
  // network response.
  useLayoutEffect(() => {
    const anchor = pendingAnchorRef.current
    const scroller = scrollRef.current
    if (!anchor || !scroller) return
    scroller.scrollTop = anchor.scrollTop + (scroller.scrollHeight - anchor.scrollHeight)
    pendingAnchorRef.current = null
  }, [oldestSeq])

  // Deep-link from conversation search: ``#msg-<seq>`` may target a message
  // older than the bootstrap tail. After the initial load completes, walk
  // backscroll until ``oldest_seq <= targetSeq``, then scroll the anchor
  // into view. Ref-guarded so we only resolve the hash once per conv open.
  const hashResolvedForRef = useRef<string | null>(null)
  const messagesLoaded = (messages?.length ?? 0) > 0
  useEffect(() => {
    if (!messagesLoaded) return
    if (typeof window === 'undefined') return
    if (hashResolvedForRef.current === conversationId) return
    hashResolvedForRef.current = conversationId
    const m = /^#msg-(\d+)$/.exec(window.location.hash)
    if (!m) return
    const targetSeq = parseInt(m[1], 10)
    if (Number.isNaN(targetSeq)) return
    // Abort the backscroll walk on conv switch / unmount so we don't keep
    // pulling pages into a conversation the user has left (which would also
    // bleed an error bubble into the foreground conv on 404).
    const controller = new AbortController()
    void (async () => {
      const client = createApiClient('')
      if (workspaceId) client.setWorkspaceId(workspaceId)
      await loadOlderUntilSeq(client, conversationId, targetSeq, controller.signal)
      if (controller.signal.aborted) return
      requestAnimationFrame(() => {
        document.getElementById(`msg-${targetSeq}`)?.scrollIntoView({ block: 'start' })
      })
    })()
    return () => {
      controller.abort()
    }
  }, [conversationId, messagesLoaded, loadOlderUntilSeq, workspaceId])

  return (
    <ScrollArea ref={scrollRef} className="flex-1 p-4" onScroll={handleScroll}>
      <div ref={contentRef} className="space-y-4 max-w-2xl mx-auto px-4 md:px-0">
        {hasMoreOlder && (
          <div className="flex justify-center py-2">
            <button
              type="button"
              onClick={handleLoadEarlier}
              disabled={isLoadingOlder}
              className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-60 transition-colors px-3 py-1 rounded-md border border-border bg-background/60"
            >
              {isLoadingOlder ? t('loadingEarlier') : t('loadEarlier')}
            </button>
          </div>
        )}
        {(messages ?? []).map((msg) => (
          // id="msg-{seq}" matches the conversation-search route's
          // matched_message_seq (cubepi_messages.seq — see
          // backend/cubebox/search/worker.py). SearchResultRow links to
          // #msg-N; the browser scrolls to this anchor when the user
          // clicks a search hit. ``seq`` is missing only on the optimistic
          // user bubble before the run is claimed — those are never search
          // targets, so we just skip the anchor.
          <div key={msg.id} id={msg.seq != null ? `msg-${msg.seq}` : undefined}>
            {msg.role === 'user' && msg.metadata?.synthetic !== true && (
              <>
                {msg.metadata?.sender_display_name && msg.metadata?.sender_user_id && (
                  <SenderBadge
                    userId={msg.metadata.sender_user_id}
                    displayName={msg.metadata.sender_display_name}
                  />
                )}
                {msg.metadata?.attachments && msg.metadata.attachments.length > 0 && (
                  <MessageAttachments
                    attachments={msg.metadata.attachments}
                    conversationId={conversationId}
                  />
                )}
                <UserMessage content={getTextContent(msg)} timestamp={msg.timestamp} />
              </>
            )}
            {msg.role === 'assistant' &&
              msg.id !== lastAssistantId &&
              (() => {
                const anchor = anchorByMessageId.get(msg.id)
                const isAnchor = anchor !== undefined
                const isLastRun = isAnchor && msg.id === lastAnchorMessageId
                return (
                  <HistoryAssistantMessage
                    message={msg}
                    subagentDataMap={subagentDataMap}
                    toolResultMap={messageScopedToolResults[msg.id] ?? historicalToolResults}
                    conversationId={conversationId}
                    workspaceId={workspaceId}
                    isGroupChat={isGroupChat}
                    activeRunId={activeRunId}
                    isStreamingTurn={isStreaming}
                    showForkAction={isAnchor}
                    turnUsage={anchor?.turnUsage ?? null}
                    turnCopyText={anchor?.copyText ?? ''}
                    isLastRun={isLastRun}
                    sessionUsage={isLastRun ? sessionUsage : null}
                    contextWindow={isLastRun ? contextWindow : null}
                    contextTokens={isLastRun ? contextTokens : null}
                    pendingConfirmMap={pendingConfirmMap}
                    onSandboxConfirm={handleSandboxConfirm}
                  />
                )
              })()}
          </div>
        ))}

        {failoverEvents.map((event, idx) => (
          <FailoverBanner key={`${event.timestamp}-${idx}`} event={event} />
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
                onCancel={handleAskUserCancel}
              />
            </div>
          </div>
        )}

        {conversationError && <RunErrorBubble data={conversationError.data} />}

        {lastRunStatus === 'stale' && (
          <div
            className="flex items-start gap-2 px-3 py-2.5 rounded
            bg-warning-surface border border-warning-border text-warning-fg text-sm"
          >
            <AlertCircle className="size-4 shrink-0 mt-0.5" />
            <span>{t('incompletePreviousAnswer')}</span>
          </div>
        )}
      </div>
    </ScrollArea>
  )
}
