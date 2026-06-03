// frontend/packages/core/src/stores/messageStore.ts
//
// All persisted Message values mirror cubepi's pydantic dump shape — content is
// always a list of typed blocks (text / thinking / tool_call), and cubebox-
// specific extras (attachments, memory snapshots, citations, subagent_events)
// ride inside `metadata`. The store builds the same shape on the streaming
// path so the in-memory view matches what bootstrap returns.
import { create } from 'zustand'
import type {
  AgentEvent,
  ArtifactEventData,
  AssistantMessage as AssistantMessageType,
  ContentBlock,
  Message,
  ReasoningEvent,
  TextDeltaEvent,
  TodoItem,
  ToolCallDeltaEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolResultMessage as ToolResultMessageType,
  UserMessage as UserMessageType,
} from '../types'
import { getTextContent } from '../types'
import type { ApiClient } from '../api'
import {
  cancelActiveRun,
  cancelSteer,
  getConversationBootstrap,
  steerRun,
  streamMessages,
  streamRun,
} from '../api'
import { useCitationStore } from './citationStore'
import { useConversationStore } from './conversationStore'

const YIELD_EVERY = 200

function yieldToEventLoop(): Promise<void> {
  const sched = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler
  if (sched && typeof sched.yield === 'function') return sched.yield()
  return new Promise((resolve) => setTimeout(resolve))
}

export interface AgentStream {
  text: string
  toolCalls: ToolCallEvent[]
  toolResults: ToolResultEvent[]
  thinking: string
  blocks: ContentBlock[]
  name: string | null
}

export interface PendingConfirm {
  question_id: string
  command: string
  matched_pattern: string | null
  timeout_seconds: number | null
  requestedAt: number
  /**
   * Run id that owns the pending request. Needed by the answer-submit route
   * to recover the right run on resume — populated from the live SSE
   * ``currentRunId`` or the bootstrap ``pending_hitl`` payload.
   */
  run_id: string
}

export interface PendingAsk {
  question_id: string
  questions: import('../types/events').AskQuestion[]
  timeout_seconds: number | null
  requestedAt: number
  /** Run id that owns the pending request. See ``PendingConfirm.run_id``. */
  run_id: string
}

export interface MessageStore {
  messages: Record<string, Message[]>
  pendingSteers: Record<string, { steerId: string; text: string }[]>
  streamAgents: Record<string, AgentStream> // "main" or "subagent:xxx"
  isStreaming: boolean
  streamingConversationId: string | null
  currentRunId: string | null
  /** Set by the answer handlers to tell the next bootstrap "don't re-seed
   * pendingAsk for this question; we just answered it, the backend's
   * `save_pending_request(None)` may not have committed yet." Cleared
   * whenever a new HITL request becomes active. */
  lastAnsweredAskQuestionId: string | null
  lastResolvedSandboxQuestionId: string | null
  lastAppliedEventId: string | null
  statusPhase: string | null
  error: string | null
  lastRunStatus: 'stale' | null
  todos: TodoItem[]
  toolStartedMap: Record<string, number>
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number; startedAt?: number; contentType?: string }
  >
  turnUsage: Record<string, import('../types').TurnUsage | null>
  sessionUsage: Record<string, import('../types').SessionUsage | null>
  contextWindow: Record<string, number | null>
  contextTokens: Record<string, number | null>
  pendingConfirmMap: Record<string, PendingConfirm>
  pendingAsk: PendingAsk | null

  loadMessages(client: ApiClient, conversationId: string): Promise<void>
  send(
    client: ApiClient,
    conversationId: string,
    content: string,
    attachmentIds?: string[],
    attachments?: import('../types').MessageAttachment[],
  ): Promise<void>
  cancelStream(client: ApiClient, conversationId: string): Promise<void>
  steer(client: ApiClient, conversationId: string, content: string): Promise<void>
  cancelSteer(client: ApiClient, conversationId: string, steerId: string): Promise<void>
  __commitTurnAndInject(conversationId: string, data: { content: string; steer_id: string }): void
  clearStream(): void
  clearLastRunStatus(): void
  /** Test hook: apply a single AgentEvent synchronously */
  __applyEvent(event: AgentEvent): void
}

let activeStreamController: AbortController | null = null

const MAIN_AGENT_KEY = 'main'

function emptyStream(name: string | null = null): AgentStream {
  return { text: '', toolCalls: [], toolResults: [], thinking: '', blocks: [], name }
}

function timestampToMs(timestamp?: string): number {
  return timestamp ? new Date(timestamp).getTime() : Date.now()
}

function compareEventIds(left: string, right: string): number {
  const [leftMs, leftSeq] = left.split('-').map(Number)
  const [rightMs, rightSeq] = right.split('-').map(Number)
  if (leftMs !== rightMs) return leftMs - rightMs
  return leftSeq - rightSeq
}

function nextEventId(current: string | null, next?: string): string | null {
  if (!next) return current
  if (!current) return next
  return compareEventIds(next, current) > 0 ? next : current
}

let _idCounter = 0
function nextMessageId(prefix: string): string {
  _idCounter += 1
  return `${prefix}-${Date.now()}-${_idCounter}`
}

/**
 * cubepi stores thinking timing as `started_at` in epoch seconds + `duration_ms`.
 * In-memory we normalize started_at to milliseconds so the renderer's
 * Date.now() math works without unit gymnastics. Idempotent: already-ms values
 * (anything > 1e12) pass through.
 */
function normalizeThinkingTiming(blocks: ContentBlock[]): ContentBlock[] {
  return blocks.map((b) => {
    if (b.type !== 'thinking' || b.started_at == null) return b
    const ms = b.started_at < 1e12 ? b.started_at * 1000 : b.started_at
    return { ...b, started_at: ms }
  })
}

function normalizeMessages(messages: Message[]): Message[] {
  return messages.map((msg) => {
    // Some legacy fixtures may omit `id`; synthesize one for React keys.
    const withId = msg.id ? msg : { ...msg, id: nextMessageId(msg.role) }
    if (
      withId.role !== 'tool_result' &&
      Array.isArray(withId.content) &&
      withId.content.some((b) => b.type === 'thinking')
    ) {
      return { ...withId, content: normalizeThinkingTiming(withId.content) } as Message
    }
    return withId
  })
}

/** Finalize the last thinking block's duration if switching to a different block type */
function finalizeLastThinking(blocks: ContentBlock[]): ContentBlock[] {
  const last = blocks[blocks.length - 1]
  if (last?.type === 'thinking' && last.started_at && !last.duration_ms) {
    const updated = [...blocks]
    updated[updated.length - 1] = { ...last, duration_ms: Date.now() - last.started_at }
    return updated
  }
  return blocks
}

/** Append content to blocks, merging with the last block of the same type. */
function appendThinkingBlock(
  blocks: ContentBlock[],
  delta: string,
  startedAt: number,
): ContentBlock[] {
  const last = blocks[blocks.length - 1]
  if (last && last.type === 'thinking') {
    const updated = [...blocks]
    updated[updated.length - 1] = { ...last, thinking: last.thinking + delta }
    return updated
  }
  return [
    ...finalizeLastThinking(blocks),
    { type: 'thinking', thinking: delta, started_at: startedAt },
  ]
}

function appendTextBlock(blocks: ContentBlock[], delta: string): ContentBlock[] {
  const last = blocks[blocks.length - 1]
  if (last && last.type === 'text') {
    const updated = [...blocks]
    updated[updated.length - 1] = { ...last, text: last.text + delta }
    return updated
  }
  return [...finalizeLastThinking(blocks), { type: 'text', text: delta }]
}

function appendToolCallBlock(
  blocks: ContentBlock[],
  name: string,
  args: Record<string, unknown>,
  toolCallId: string,
): ContentBlock[] {
  const finalized = finalizeLastThinking(blocks)
  const exactMatchIndex = finalized.findIndex(
    (block) => block.type === 'tool_call_streaming' && block.tool_call_id === toolCallId,
  )
  // Fallback: streaming block whose id was never populated (name='' when the first delta
  // didn't carry identity fields) also qualifies — but only when exactly one such slot
  // exists. With multiple unnamed slots we cannot tell which belongs to this completion,
  // so we leave them unmatched and let the idempotency guard below handle duplicates.
  let fallbackMatchIndex = -1
  let unnamedFallbackIndex = -1
  let unnamedFallbackCount = 0
  for (let i = finalized.length - 1; i >= 0; i--) {
    const block = finalized[i]
    if (block.type === 'tool_call_streaming' && block.tool_call_id === null) {
      if (block.name === name) {
        fallbackMatchIndex = i
        break
      }
      if (block.name === '') {
        unnamedFallbackCount++
        if (unnamedFallbackIndex === -1) {
          unnamedFallbackIndex = i
        }
      }
    }
  }
  const safeUnnamedFallback = unnamedFallbackCount === 1 ? unnamedFallbackIndex : -1
  const matchIndex =
    exactMatchIndex >= 0
      ? exactMatchIndex
      : fallbackMatchIndex >= 0
        ? fallbackMatchIndex
        : safeUnnamedFallback

  const nextBlocks =
    matchIndex >= 0 ? finalized.filter((_, index) => index !== matchIndex) : finalized

  // Guard: if no streaming block was found to replace, don't add a duplicate completed block.
  if (matchIndex < 0 && nextBlocks.some((b) => b.type === 'tool_call' && b.id === toolCallId)) {
    return nextBlocks
  }

  return [...nextBlocks, { type: 'tool_call', id: toolCallId, name, arguments: args }]
}

function normalizeTodoStatus(status: unknown): TodoItem['status'] {
  return status === 'in_progress' || status === 'completed' ? status : 'pending'
}

function parseTodosFromToolCall(args: Record<string, unknown>): TodoItem[] {
  const rawTodos = Array.isArray(args.todos) ? args.todos : []
  const todos: TodoItem[] = []

  for (const todo of rawTodos) {
    if (!todo || typeof todo !== 'object') continue
    const raw = todo as { content?: unknown; status?: unknown }
    const description = typeof raw.content === 'string' ? raw.content.trim() : ''
    if (!description) continue
    todos.push({
      id: null,
      description,
      status: normalizeTodoStatus(raw.status),
    })
  }

  return todos
}

function buildPendingUserMessage(runId: string, content: string): UserMessageType {
  return {
    id: `pending-${runId}`,
    role: 'user',
    content: [{ type: 'text', text: content }],
    timestamp: Date.now() / 1000,
    metadata: {},
  }
}

/**
 * History returned by `/bootstrap` may not yet contain the active run's
 * user message if cubepi's checkpointer is briefly behind the Redis
 * stream — append a placeholder so the user's prompt renders without
 * waiting for the next poll. Anything else in `messages` reflects
 * cubepi's committed log; the SSE reattach is cursor'd past
 * `active_run.last_event_id` so the stream will not replay history,
 * which means there is nothing to trim from the tail.
 *
 * `startedAt` (ISO from the run record) disambiguates a same-content
 * user message from a prior turn; we only bind to a history entry
 * created at or after the run was claimed.
 */
export function trimHistoryForActiveRun(
  messages: Message[],
  runId: string,
  content: string,
  startedAt: string | null,
): Message[] {
  const startedAtMs = startedAt ? Date.parse(startedAt) : NaN
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.role !== 'user') continue
    const msgMs = msg.timestamp != null ? msg.timestamp * 1000 : NaN
    if (Number.isFinite(startedAtMs) && Number.isFinite(msgMs) && msgMs < startedAtMs) {
      break
    }
    if (getTextContent(msg) === content) return messages
    // Not the original (e.g. a steer turn) — keep scanning back.
  }
  return [...messages, buildPendingUserMessage(runId, content)]
}

function restoreTodosFromHistory(messages: Message[]): TodoItem[] {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.role !== 'assistant') continue
    for (const block of msg.content) {
      if (block.type === 'tool_call' && block.name === 'write_todos') {
        return parseTodosFromToolCall(block.arguments)
      }
    }
  }
  return []
}

function hydrateCitationsFromHistory(conversationId: string, messages: Message[]): void {
  for (const msg of messages) {
    if (msg.role !== 'tool_result') continue
    // CitationMiddleware persists citations on ToolResultMessage.details
    // (cubebox/middleware/citation.py:169 — AfterToolCallResult(details={"citations": [...]})).
    // metadata.citations only exists for in-memory finalized messages.
    const details = msg.details as
      | { citations?: import('../types').CitationData[] }
      | null
      | undefined
    const citations = details?.citations ?? msg.metadata?.citations
    if (citations && citations.length > 0) {
      useCitationStore.getState().loadCitations(conversationId, citations)
    }
  }
}

/**
 * Batched state updater: collects multiple set() calls within a single microtask
 * and flushes them as one Zustand update. This prevents N SSE events arriving in
 * one chunk from causing N separate re-renders.
 */
function createBatcher(set: (updater: (s: MessageStore) => Partial<MessageStore>) => void) {
  let pending: Array<(s: MessageStore) => Partial<MessageStore>> = []
  let scheduled = false

  const flush = () => {
    if (pending.length === 0) return
    const batch = pending
    pending = []
    scheduled = false
    set((state) => {
      let merged = state
      for (const fn of batch) {
        merged = { ...merged, ...fn(merged) }
      }
      return merged
    })
  }

  const batchedSet = (updater: (s: MessageStore) => Partial<MessageStore>) => {
    pending.push(updater)
    if (!scheduled) {
      scheduled = true
      queueMicrotask(flush)
    }
  }

  return { batchedSet, flush }
}

function applyStreamEvent(state: MessageStore, event: AgentEvent): Partial<MessageStore> {
  const eventId = event.event_id
  if (
    eventId &&
    state.lastAppliedEventId &&
    compareEventIds(eventId, state.lastAppliedEventId) <= 0
  ) {
    return {}
  }

  const agentKey = event.agent_id ?? MAIN_AGENT_KEY
  const lastAppliedEventId = nextEventId(state.lastAppliedEventId, eventId)
  const base = lastAppliedEventId ? { lastAppliedEventId } : {}

  if (event.type === 'text_delta') {
    const e = event as TextDeltaEvent
    const prev = state.streamAgents[agentKey] ?? emptyStream(event.agent_name)
    return {
      ...base,
      streamAgents: {
        ...state.streamAgents,
        [agentKey]: {
          ...prev,
          text: prev.text + e.data.content,
          blocks: appendTextBlock(prev.blocks, e.data.content),
        },
      },
    }
  }

  // SSE event name stays `reasoning` for backend protocol compatibility; we map
  // it into a `thinking` block to match cubepi's ThinkingContent.
  if (event.type === 'reasoning') {
    const e = event as ReasoningEvent
    const prev = state.streamAgents[agentKey] ?? emptyStream(event.agent_name)
    return {
      ...base,
      streamAgents: {
        ...state.streamAgents,
        [agentKey]: {
          ...prev,
          thinking: prev.thinking + e.data.content,
          blocks: appendThinkingBlock(prev.blocks, e.data.content, timestampToMs(event.timestamp)),
        },
      },
    }
  }

  if (event.type === 'tool_call') {
    const e = event as ToolCallEvent
    const prev = state.streamAgents[agentKey] ?? emptyStream(event.agent_name)
    const existingStartedAt = state.toolStartedMap[e.data.tool_call_id]
    const nextTodos =
      e.data.name === 'write_todos' ? parseTodosFromToolCall(e.data.arguments) : state.todos

    return {
      ...base,
      todos: nextTodos,
      toolStartedMap: {
        ...state.toolStartedMap,
        [e.data.tool_call_id]:
          existingStartedAt ?? timestampToMs(e.data.started_at ?? event.timestamp),
      },
      streamAgents: {
        ...state.streamAgents,
        [agentKey]: {
          ...prev,
          toolCalls: [...prev.toolCalls, e],
          blocks: appendToolCallBlock(
            prev.blocks,
            e.data.name,
            e.data.arguments,
            e.data.tool_call_id,
          ),
        },
      },
    }
  }

  if (event.type === 'tool_call_delta') {
    const e = event as ToolCallDeltaEvent
    const prev = state.streamAgents[agentKey] ?? emptyStream(event.agent_name)
    const idx = e.data.index ?? 0
    const blocks = [...prev.blocks]
    const startedAt = timestampToMs(event.timestamp)
    const nextToolStartedMap =
      e.data.tool_call_id && !state.toolStartedMap[e.data.tool_call_id]
        ? {
            ...state.toolStartedMap,
            [e.data.tool_call_id]: startedAt,
          }
        : state.toolStartedMap

    const existingIdx = blocks.findIndex(
      (block) => block.type === 'tool_call_streaming' && block.index === idx,
    )

    if (existingIdx >= 0) {
      const existing = blocks[existingIdx] as Extract<ContentBlock, { type: 'tool_call_streaming' }>
      blocks[existingIdx] = {
        ...existing,
        args_text: existing.args_text + (e.data.args_delta || ''),
        tool_call_id: e.data.tool_call_id ?? existing.tool_call_id,
      }
      return {
        ...base,
        toolStartedMap: nextToolStartedMap,
        streamAgents: {
          ...state.streamAgents,
          [agentKey]: { ...prev, blocks },
        },
      }
    }

    const finalized = finalizeLastThinking(blocks)
    finalized.push({
      type: 'tool_call_streaming',
      name: e.data.name ?? '',
      args_text: e.data.args_delta || '',
      tool_call_id: e.data.tool_call_id ?? null,
      index: idx,
    })
    return {
      ...base,
      toolStartedMap: nextToolStartedMap,
      streamAgents: {
        ...state.streamAgents,
        [agentKey]: { ...prev, blocks: finalized },
      },
    }
  }

  if (event.type === 'tool_result') {
    const e = event as ToolResultEvent
    const tcId = e.data.tool_call_id ?? ''
    const newMap = { ...state.toolResultMap }
    if (tcId) {
      newMap[tcId] = {
        content: e.data.content,
        receivedAt: timestampToMs(event.timestamp),
        startedAt:
          state.toolStartedMap[tcId] ??
          (e.data.started_at ? timestampToMs(e.data.started_at) : undefined),
        contentType: e.data.content_type,
      }
    }

    // ask_user tool result (including timeout/error) means the ask is resolved.
    // The SSE dict key is "name" (runtime); TypeScript type says "tool_name" — cast to access it.
    const clearAsk =
      (e.data as unknown as { name?: string }).name === 'ask_user' ? { pendingAsk: null } : {}
    return {
      ...base,
      ...clearAsk,
      toolResultMap: newMap,
      streamAgents: {
        ...state.streamAgents,
        [agentKey]: {
          ...(state.streamAgents[agentKey] ?? emptyStream(event.agent_name)),
          toolResults: [...(state.streamAgents[agentKey]?.toolResults ?? []), e],
        },
      },
    }
  }

  if (event.type === 'status') {
    return {
      ...base,
      statusPhase: (event.data as { phase: string }).phase,
    }
  }

  if (event.type === 'artifact' || event.type === 'citation') {
    return base
  }

  if (event.type === 'sandbox_confirm_request') {
    const d = event.data as {
      question_id: string
      tool_call_id: string
      command: string
      matched_pattern: string | null
      timeout_seconds: number | null
      run_id?: string
    }
    if (!d.tool_call_id) return base
    // Live SSE: the active run drives this event. The event payload may not
    // carry run_id today, so fall back to the store's currentRunId (set when
    // the run was claimed). Empty string surfaces the failure mode if both
    // are absent rather than silently shipping a fake id.
    const runId = d.run_id ?? state.currentRunId ?? ''
    return {
      ...base,
      pendingConfirmMap: {
        ...state.pendingConfirmMap,
        [d.tool_call_id]: {
          question_id: d.question_id,
          command: d.command,
          matched_pattern: d.matched_pattern ?? null,
          timeout_seconds: d.timeout_seconds ?? null,
          requestedAt: event.timestamp ? new Date(event.timestamp).getTime() : Date.now(),
          run_id: runId,
        },
      },
      // Mirror ask_user_request: a fresh confirm question wipes the
      // "we just resolved the previous one" guard.
      lastResolvedSandboxQuestionId: null,
    }
  }

  if (event.type === 'sandbox_confirm_resolved') {
    const d = event.data as {
      question_id: string
      decision?: 'approve' | 'deny' | 'policy_overridden' | null
      reason?: string | null
    }
    const tcId = Object.entries(state.pendingConfirmMap).find(
      ([, v]) => v.question_id === d.question_id,
    )?.[0]
    if (!tcId) return base
    if (d.decision === 'policy_overridden') {
      // Synthetic resolve from the respond path: the org sandbox policy
      // changed during the pause, so the pending confirm was force-cleared.
      // No inline-note primitive exists in the message list today; the card
      // disappearance is the load-bearing UX. Surface a console warn so the
      // event is greppable in dev tools / Playwright traces (T15).
      console.warn('[sandbox_confirm_resolved] policy_overridden — pending cleared', {
        question_id: d.question_id,
        tool_call_id: tcId,
        reason: d.reason,
      })
    }
    const next = { ...state.pendingConfirmMap }
    delete next[tcId]
    return { ...base, pendingConfirmMap: next }
  }

  if (event.type === 'ask_user_request') {
    const d = event.data as {
      question_id: string
      questions: import('../types/events').AskQuestion[]
      timeout_seconds: number | null
      run_id?: string
    }
    if (state.pendingAsk?.question_id === d.question_id) return base // idempotent
    // See sandbox_confirm_request above for the run_id source-of-truth note.
    const runId = d.run_id ?? state.currentRunId ?? ''
    return {
      ...base,
      pendingAsk: {
        question_id: d.question_id,
        questions: d.questions,
        timeout_seconds: d.timeout_seconds ?? null,
        requestedAt: event.timestamp ? new Date(event.timestamp).getTime() : Date.now(),
        run_id: runId,
      },
      // Fresh question — drop the "we just answered the previous one" marker
      // so a subsequent bootstrap can seed THIS form if the page reloads
      // before the user gets to it.
      lastAnsweredAskQuestionId: null,
    }
  }

  if (event.type === 'ask_user_resolved') {
    const d = event.data as {
      question_id: string
      cancelled?: boolean
      reason?: string | null
    }
    if (state.pendingAsk?.question_id !== d.question_id) return base
    if (d.cancelled && d.reason === 'policy_overridden') {
      // Synthetic resolve from the respond path (T12): the org sandbox policy
      // changed during the pause, so the pending ask was force-cancelled.
      // See sandbox_confirm_resolved above for the inline-note caveat.
      console.warn('[ask_user_resolved] policy_overridden — pending cleared', {
        question_id: d.question_id,
        reason: d.reason,
      })
    }
    return { ...base, pendingAsk: null }
  }

  return base
}

/**
 * Build the assistant (+ tool) messages for a finished/interrupted turn from the
 * current streaming buckets. Pure — reads its inputs, allocates message ids, and
 * returns the pieces; the caller decides how to append them and clear state.
 * Returns `assistantMessage: null` when there is no main stream to finalize.
 */
function buildTurnMessages(
  agents: Record<string, AgentStream>,
  toolResultMap: MessageStore['toolResultMap'],
  turnUsage: import('../types').TurnUsage | null,
  stopReason: AssistantMessageType['stop_reason'] = 'stop',
): { assistantMessage: AssistantMessageType | null; toolMessages: ToolResultMessageType[] } {
  const mainStream = agents[MAIN_AGENT_KEY]
  if (!mainStream) return { assistantMessage: null, toolMessages: [] }

  const finalBlocks = finalizeLastThinking(mainStream.blocks).filter(
    (block) => block.type !== 'tool_call_streaming',
  )

  const usage = turnUsage
  const assistantMessage: AssistantMessageType = {
    id: nextMessageId('assistant'),
    role: 'assistant',
    content: finalBlocks,
    stop_reason: stopReason,
    usage: usage
      ? {
          input_tokens: usage.input_tokens,
          output_tokens: usage.output_tokens,
          cache_read_tokens: usage.cache_read_tokens,
          cache_write_tokens: usage.cache_write_tokens,
        }
      : null,
    timestamp: Date.now() / 1000,
    metadata: {},
  }

  const toolMessages: ToolResultMessageType[] = []

  // Collect subagent args so we can attach role/task to each subagent tool message.
  const subagentArgs: Record<string, { role?: string; task?: string }> = {}
  for (const block of finalBlocks) {
    if (block.type === 'tool_call' && block.name === 'subagent') {
      const args = block.arguments as { role?: string; task?: string }
      subagentArgs[`subagent:${block.id}`] = args
    }
  }

  // Persist main-agent tool results into history so the just-finished message
  // remains interactive after streamingConversationId clears. Skip subagent
  // tool results — those get richer messages from the loop below.
  for (const tr of mainStream.toolResults) {
    const tcId = tr.data.tool_call_id
    if (!tcId || tr.data.tool_name === 'subagent') continue
    const mapEntry = toolResultMap[tcId]
    const receivedAtMs =
      mapEntry?.receivedAt ?? (tr.timestamp ? new Date(tr.timestamp).getTime() : Date.now())
    toolMessages.push({
      id: nextMessageId('tool'),
      role: 'tool_result',
      content: [{ type: 'text', text: tr.data.content ?? mapEntry?.content ?? '' }],
      tool_call_id: tcId,
      tool_name: tr.data.tool_name ?? '',
      timestamp: receivedAtMs / 1000,
      metadata: {},
    })
  }

  for (const [key, agentStream] of Object.entries(agents)) {
    if (key === MAIN_AGENT_KEY) continue
    const toolCallId = key.startsWith('subagent:') ? key.slice(9) : key
    const args = subagentArgs[key]
    toolMessages.push({
      id: nextMessageId('tool'),
      role: 'tool_result',
      content: [{ type: 'text', text: agentStream.text || '' }],
      tool_call_id: toolCallId,
      tool_name: 'subagent',
      timestamp: Date.now() / 1000,
      metadata: {
        subagent_events: {
          text: agentStream.text,
          tool_calls: agentStream.toolCalls.map((tc) => ({
            name: tc.data.name,
            arguments: tc.data.arguments,
            id: tc.data.tool_call_id,
            started_at: tc.data.started_at ?? (tc.timestamp || null),
          })),
          tool_results: agentStream.toolResults
            .map((tr) => {
              const mapEntry = toolResultMap[tr.data.tool_call_id ?? '']
              return {
                tool_name: tr.data.tool_name ?? '',
                tool_call_id: tr.data.tool_call_id ?? '',
                content: tr.data.content ?? '',
                content_type: tr.data.content_type ?? mapEntry?.contentType ?? null,
                started_at: tr.data.started_at ?? null,
                completed_at: tr.timestamp || null,
              }
            })
            .filter((tr) => tr.tool_call_id),
          thinking: agentStream.thinking,
          role: args?.role,
          task: args?.task,
        },
      },
    })
  }

  return { assistantMessage, toolMessages }
}

async function finalizeCompletedStream(
  get: () => MessageStore,
  set: (partial: Partial<MessageStore> | ((state: MessageStore) => Partial<MessageStore>)) => void,
  conversationId: string,
  stopReason: AssistantMessageType['stop_reason'] = 'stop',
): Promise<void> {
  const { assistantMessage, toolMessages } = buildTurnMessages(
    get().streamAgents,
    get().toolResultMap,
    get().turnUsage[conversationId] ?? null,
    stopReason,
  )

  if (!assistantMessage) {
    set((state) => ({
      isStreaming: false,
      pendingConfirmMap: {},
      pendingAsk: null,
      streamingConversationId: null,
      currentRunId: null,
      statusPhase: null,
      pendingSteers: { ...state.pendingSteers, [conversationId]: [] },
    }))
    return
  }

  set((state) => ({
    messages: {
      ...state.messages,
      [conversationId]: [
        ...(state.messages[conversationId] ?? []),
        assistantMessage,
        ...toolMessages,
      ],
    },
    // Reset the in-flight stream now that the content lives in messages.
    // Leaving streamAgents populated forces MessageList's lastAssistantId
    // skip to fire — which can hide a DIFFERENT history assistant if the
    // resume turn's mainStream content arrives before cubepi commits the
    // resume assistant to the message log.
    streamAgents: {},
    toolStartedMap: {},
    isStreaming: false,
    pendingConfirmMap: {},
    pendingAsk: null,
    streamingConversationId: null,
    currentRunId: null,
    statusPhase: null,
    pendingSteers: { ...state.pendingSteers, [conversationId]: [] },
  }))
}

async function finalizePausedStream(
  get: () => MessageStore,
  set: (partial: Partial<MessageStore> | ((state: MessageStore) => Partial<MessageStore>)) => void,
  conversationId: string,
): Promise<void> {
  // Paused HITL: the run task is over (worker released after auto-detach),
  // but the conversation is still parked on a pending question. Commit the
  // in-flight assistant message + tool results the same way the completed
  // path does, BUT preserve pendingAsk / pendingConfirmMap so the card
  // stays visible until the user answers or cancels. currentRunId stays
  // set too — the resume turn reuses the same run_id, so the SSE consumer
  // that re-attaches after the user submits will continue on this id.
  //
  // Keep `streamingConversationId === conversationId` while a pending
  // ask/confirm is still attached: MessageList gates `<AskUserCard>` on
  // that equality, and clearing it here would hide the card the user
  // needs to answer. Mirrors bootstrap's pending_hitl branch which
  // marks the conversation "streaming-attached" even with no live SSE.
  const state0 = get()
  const stillAttached =
    state0.pendingAsk !== null || Object.keys(state0.pendingConfirmMap).length > 0
  const nextStreamingConversationId = stillAttached ? conversationId : null

  const { assistantMessage, toolMessages } = buildTurnMessages(
    state0.streamAgents,
    state0.toolResultMap,
    state0.turnUsage[conversationId] ?? null,
    'stop',
  )

  if (!assistantMessage) {
    set((state) => ({
      isStreaming: false,
      streamingConversationId: nextStreamingConversationId,
      statusPhase: null,
      pendingSteers: { ...state.pendingSteers, [conversationId]: [] },
    }))
    return
  }

  set((state) => ({
    messages: {
      ...state.messages,
      [conversationId]: [
        ...(state.messages[conversationId] ?? []),
        assistantMessage,
        ...toolMessages,
      ],
    },
    // Same rationale as finalizeCompletedStream — drop the in-flight
    // stream now that history owns the content.
    streamAgents: {},
    toolStartedMap: {},
    isStreaming: false,
    streamingConversationId: nextStreamingConversationId,
    statusPhase: null,
    pendingSteers: { ...state.pendingSteers, [conversationId]: [] },
  }))
}

async function consumeRunStream(
  client: ApiClient,
  conversationId: string,
  runId: string,
  lastEventId: string | undefined,
  set: (partial: Partial<MessageStore> | ((state: MessageStore) => Partial<MessageStore>)) => void,
  get: () => MessageStore,
  signal?: AbortSignal,
): Promise<void> {
  const { batchedSet, flush } = createBatcher(
    set as (updater: (s: MessageStore) => Partial<MessageStore>) => void,
  )
  let shouldFinalize = true
  let sawDone = false
  let sawPausedDone = false
  let processed = 0

  try {
    for await (const event of streamRun(client, conversationId, runId, lastEventId, signal)) {
      const state = get()
      if (state.currentRunId !== runId) {
        shouldFinalize = false
        return
      }
      if (event.event_id && state.lastAppliedEventId) {
        if (compareEventIds(event.event_id, state.lastAppliedEventId) <= 0) continue
      }

      if (event.type === 'artifact') {
        const artifactData = event.data as unknown as ArtifactEventData
        if (artifactData.artifact) {
          const { useArtifactStore } = await import('./artifactStore')
          useArtifactStore.getState().addOrUpdate(conversationId, artifactData.artifact)
        }
      } else if (event.type === 'citation') {
        const citationData = event.data as unknown as import('../types').CitationData
        useCitationStore.getState().addCitation(conversationId, citationData)
      } else if (event.type === 'error') {
        const errData = event.data as { message: string; details?: string }
        set((s) => ({
          error: errData.details || errData.message,
          isStreaming: false,
          pendingConfirmMap: {},
          pendingAsk: null,
          streamingConversationId: null,
          currentRunId: null,
          statusPhase: null,
          lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
          pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
        }))
        break
      } else if (event.type === 'done') {
        const usage = (event.data as Record<string, unknown>).usage as
          | import('../types').UsageSummary
          | undefined
        const paused = (event.data as Record<string, unknown>).paused === true
        const usageUpdate: Partial<MessageStore> = {
          lastAppliedEventId: nextEventId(get().lastAppliedEventId, event.event_id),
        }
        if (usage) {
          usageUpdate.turnUsage = {
            ...get().turnUsage,
            [conversationId]: usage.turn,
          }
          usageUpdate.sessionUsage = {
            ...get().sessionUsage,
            [conversationId]: usage.session,
          }
          usageUpdate.contextWindow = {
            ...get().contextWindow,
            [conversationId]: usage.context_window,
          }
          usageUpdate.contextTokens = {
            ...get().contextTokens,
            [conversationId]: usage.context_tokens ?? null,
          }
        }
        set(usageUpdate)
        // paused: the run task is over but the conversation is parked
        // on a pending HITL question — keep pendingAsk / pendingConfirmMap
        // alive so the card stays visible until the user answers or cancels.
        if (paused) {
          sawPausedDone = true
        } else {
          sawDone = true
        }
        break
      } else if (event.type === 'injected_message') {
        const d = event.data as { content: string; steer_id: string }
        // Flush batched stream mutations so the commit reads fully-applied
        // streamAgents, not a stale snapshot.
        flush()
        set((s) => ({
          lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
        }))
        get().__commitTurnAndInject(conversationId, d)
        continue
      }

      batchedSet((s) => applyStreamEvent(s, event))
      if (++processed % YIELD_EVERY === 0) {
        await yieldToEventLoop()
      }
    }
  } catch (err) {
    set({ error: (err as Error).message })
  } finally {
    flush()
    if (shouldFinalize && get().currentRunId === runId) {
      if (sawPausedDone) {
        await finalizePausedStream(get, set, conversationId)
      } else if (sawDone) {
        await finalizeCompletedStream(get, set, conversationId)
      }
    }
  }
}

export const useMessageStore = create<MessageStore>((set, get) => ({
  messages: {},
  pendingSteers: {},
  streamAgents: {},
  isStreaming: false,
  streamingConversationId: null,
  currentRunId: null,
  lastAnsweredAskQuestionId: null,
  lastResolvedSandboxQuestionId: null,
  lastAppliedEventId: null,
  statusPhase: null,
  error: null,
  lastRunStatus: null,
  todos: [],
  toolStartedMap: {},
  toolResultMap: {},
  pendingConfirmMap: {},
  pendingAsk: null,
  turnUsage: {},
  sessionUsage: {},
  contextWindow: {},
  contextTokens: {},

  async loadMessages(client: ApiClient, conversationId: string) {
    const state = get()
    if (state.isStreaming && state.streamingConversationId === conversationId) return

    try {
      const bootstrap = await getConversationBootstrap(client, conversationId)
      const current = get()
      if (current.isStreaming && current.streamingConversationId === conversationId) return

      let messages = normalizeMessages(bootstrap.messages ?? [])
      if (bootstrap.active_run?.user_message) {
        messages = trimHistoryForActiveRun(
          messages,
          bootstrap.active_run.run_id,
          bootstrap.active_run.user_message,
          bootstrap.active_run.started_at ?? null,
        )
      }

      const restoredTodos = restoreTodosFromHistory(messages)
      hydrateCitationsFromHistory(conversationId, messages)
      const usageSummary = bootstrap.usage_summary
      const newTurnUsage = {
        ...get().turnUsage,
        [conversationId]: (usageSummary?.turn ?? null) as import('../types').TurnUsage | null,
      }
      const newSessionUsage = {
        ...get().sessionUsage,
        [conversationId]: (usageSummary?.session ?? null) as import('../types').SessionUsage | null,
      }
      const newContextWindow = {
        ...get().contextWindow,
        [conversationId]: usageSummary?.context_window ?? null,
      }
      const newContextTokens = {
        ...get().contextTokens,
        [conversationId]: usageSummary?.context_tokens ?? null,
      }
      const nextStreamAgents: Record<string, AgentStream> = bootstrap.active_run
        ? { [MAIN_AGENT_KEY]: emptyStream() }
        : {}

      // Cold-start fallback: when the Redis event log has aged out, the
      // backend returns the unresolved HITL request inline. Seed the same
      // pendingAsk / pendingConfirmMap slots the live SSE path populates so
      // the card re-renders on refresh without replaying events.
      //
      // Post-answer race: when the user just submitted an answer, the
      // optimistic clear set pendingAsk = null, but the bootstrap that
      // follows can race the backend's `save_pending_request(None)` —
      // the DB pending row is still there, so this code would re-seed
      // the form for the same run_id and the user would see a momentary
      // re-flash of the question they already answered. Skip the seed
      // when the store is already tracking this exact run; the SSE
      // reattach (which starts strictly after the paused-done event)
      // will land the ask_user_resolved any moment now anyway.
      const pending = bootstrap.pending_hitl ?? null
      const currentState = get()
      // If the user just answered this exact question, the backend's
      // `save_pending_request(None)` may not have committed yet when
      // bootstrap fetched — DB pending_hitl is stale. Skip the re-seed
      // so the form doesn't flash back. SSE delivers
      // `ask_user_resolved` shortly after either way.
      const alreadyAnsweredThisQuestion =
        pending !== null && pending.question_id === currentState.lastAnsweredAskQuestionId
      const alreadyResolvedThisConfirm =
        pending !== null &&
        pending.kind === 'sandbox_confirm' &&
        pending.question_id === currentState.lastResolvedSandboxQuestionId
      const skipSeed = alreadyAnsweredThisQuestion || alreadyResolvedThisConfirm
      let seedPendingAsk: PendingAsk | null = null
      let seedPendingConfirmMap: Record<string, PendingConfirm> = {}
      if (pending && pending.kind === 'ask_user' && !skipSeed) {
        const requestedAt = Date.parse(pending.requested_at)
        seedPendingAsk = {
          question_id: pending.question_id,
          questions: pending.questions,
          timeout_seconds: null,
          requestedAt: Number.isNaN(requestedAt) ? Date.now() : requestedAt,
          run_id: pending.run_id,
        }
      } else if (pending && pending.kind === 'sandbox_confirm' && !skipSeed) {
        const requestedAt = Date.parse(pending.requested_at)
        seedPendingConfirmMap = {
          [pending.tool_call_id]: {
            question_id: pending.question_id,
            command: pending.command,
            matched_pattern: pending.matched_pattern,
            timeout_seconds: null,
            requestedAt: Number.isNaN(requestedAt) ? Date.now() : requestedAt,
            run_id: pending.run_id,
          },
        }
      }

      // pending_hitl fallback: when the Redis active-run key has aged out
      // during a long pause, bootstrap.active_run is null but the DB
      // pending + run_id ARE still recoverable (via pending_hitl). Treat
      // the pending_hitl payload as "this conversation is paused on
      // run_id, the card must render, and we should tail the run stream
      // so a resume turn from any worker reaches this tab".
      //
      // MessageList gates AskUserCard on `streamingConversationId ===
      // conversationId`; without this, an ask_user paused beyond Redis
      // TTL has its pendingAsk seeded but the card stays hidden.
      const pendingHitlRunId = bootstrap.pending_hitl?.run_id ?? null
      const hasPendingHitl = pendingHitlRunId !== null
      const streamRunId = bootstrap.active_run?.run_id ?? pendingHitlRunId
      const streamingActive = !!bootstrap.active_run || hasPendingHitl
      // Paused HITL: SSE is attached but the worker has detached. Don't
      // light up "is streaming" indicators (typing dots etc.); the
      // <AskUserCard> / composer lock already convey state. `pendingAsk`
      // and `pendingConfirmMap` are the truth signals here. We keep
      // `streamingConversationId` set so MessageList's `pendingAsk &&
      // streamingConversationId === conversationId` gate still fires.
      const isPaused = bootstrap.active_run?.status === 'paused_hitl' || hasPendingHitl
      const isStreamingActive = streamingActive && !isPaused
      // `bootstrap.messages` already includes everything up to the latest
      // event in the Redis stream at fetch time. The SSE reattach should
      // pick up STRICTLY-NEW events from here; without this cursor the
      // post-answer reattach replays the paused turn (doubling the
      // assistant message into streamAgents) and hits the paused `done`
      // event mid-replay, breaking out of consumeRunStream before any
      // resume-turn events can be read. Seed both the XREAD cursor and
      // the in-store dedupe guard from `active_run.last_event_id`.
      const streamCursor = bootstrap.active_run?.last_event_id ?? null

      set((s) => ({
        messages: { ...s.messages, [conversationId]: messages },
        todos: restoredTodos,
        error: null,
        lastRunStatus: bootstrap.last_run_status ?? null,
        streamAgents: nextStreamAgents,
        pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
        toolStartedMap: {},
        toolResultMap: {},
        // When `skipSeed` fired (we just answered/cancelled this exact
        // question), preserve the current pendingAsk / pendingConfirmMap
        // instead of clearing them. The form stays mounted in its
        // submitting/cancelling state until the SSE `ask_user_resolved`
        // event arrives, which avoids the visible blank gap that
        // optimistic-clear used to produce.
        pendingConfirmMap: skipSeed ? s.pendingConfirmMap : seedPendingConfirmMap,
        pendingAsk: skipSeed ? s.pendingAsk : seedPendingAsk,
        isStreaming: isStreamingActive,
        streamingConversationId: streamingActive ? conversationId : null,
        currentRunId: streamRunId,
        lastAppliedEventId: streamCursor,
        statusPhase: null,
        turnUsage: newTurnUsage,
        sessionUsage: newSessionUsage,
        contextWindow: newContextWindow,
        contextTokens: newContextTokens,
      }))

      if (streamRunId !== null) {
        activeStreamController?.abort()
        const controller = new AbortController()
        activeStreamController = controller
        const runId = streamRunId
        const lastEventId = streamCursor ?? undefined
        queueMicrotask(() => {
          void consumeRunStream(
            client,
            conversationId,
            runId,
            lastEventId,
            set,
            get,
            controller.signal,
          ).finally(() => {
            if (activeStreamController === controller) {
              activeStreamController = null
            }
          })
        })
      }
    } catch (err) {
      set({ error: (err as Error).message })
    }
  },

  async send(
    client: ApiClient,
    conversationId: string,
    content: string,
    attachmentIds?: string[],
    attachments?: import('../types').MessageAttachment[],
  ) {
    const isFirstTurn = (get().messages[conversationId] ?? []).length === 0
    if (isFirstTurn && content.trim()) {
      void useConversationStore.getState().generateTitle(client, conversationId, content)
    }

    const userMessage: UserMessageType = {
      id: nextMessageId('user-temp'),
      role: 'user',
      content: [{ type: 'text', text: content }],
      timestamp: Date.now() / 1000,
      metadata: attachments && attachments.length > 0 ? { attachments } : {},
    }

    set((state) => ({
      messages: {
        ...state.messages,
        [conversationId]: [...(state.messages[conversationId] ?? []), userMessage],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      isStreaming: true,
      streamingConversationId: conversationId,
      currentRunId: null,
      lastAppliedEventId: null,
      statusPhase: null,
      error: null,
      lastRunStatus: null,
      todos: [],
      toolStartedMap: {},
      toolResultMap: {},
      pendingConfirmMap: {},
      pendingAsk: null,
      turnUsage: { ...state.turnUsage, [conversationId]: null },
    }))

    const { batchedSet, flush } = createBatcher(
      set as (updater: (s: MessageStore) => Partial<MessageStore>) => void,
    )
    let sawDone = false
    let sawPausedDone = false
    activeStreamController?.abort()
    const controller = new AbortController()
    activeStreamController = controller

    let retried = false
    let streamSource = streamMessages(
      client,
      conversationId,
      content,
      attachmentIds,
      controller.signal,
    )

    let processed = 0
    try {
      outer: for (;;) {
        for await (const event of streamSource) {
          if (event.event_id && get().lastAppliedEventId) {
            if (compareEventIds(event.event_id, get().lastAppliedEventId!) <= 0) continue
          }

          if (event.type === 'artifact') {
            const artifactData = event.data as unknown as ArtifactEventData
            if (artifactData.artifact) {
              const { useArtifactStore } = await import('./artifactStore')
              useArtifactStore.getState().addOrUpdate(conversationId, artifactData.artifact)
            }
          } else if (event.type === 'citation') {
            const citationData = event.data as unknown as import('../types').CitationData
            useCitationStore.getState().addCitation(conversationId, citationData)
          } else if (event.type === 'error') {
            const errData = event.data as { message: string; details?: string }
            if (!retried && !sawDone && errData.message.includes('409')) {
              retried = true
              await new Promise((r) => setTimeout(r, 400))
              streamSource = streamMessages(
                client,
                conversationId,
                content,
                attachmentIds,
                controller.signal,
              )
              continue outer
            }
            set((s) => ({
              error: errData.details || errData.message,
              isStreaming: false,
              pendingConfirmMap: {},
              pendingAsk: null,
              streamingConversationId: null,
              currentRunId: null,
              statusPhase: null,
              lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
              pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
            }))
            break outer
          } else if (event.type === 'done') {
            const usage = (event.data as Record<string, unknown>).usage as
              | import('../types').UsageSummary
              | undefined
            const paused = (event.data as Record<string, unknown>).paused === true
            const usageUpdate: Partial<MessageStore> = {
              lastAppliedEventId: nextEventId(get().lastAppliedEventId, event.event_id),
            }
            if (usage) {
              usageUpdate.turnUsage = {
                ...get().turnUsage,
                [conversationId]: usage.turn,
              }
              usageUpdate.sessionUsage = {
                ...get().sessionUsage,
                [conversationId]: usage.session,
              }
              usageUpdate.contextWindow = {
                ...get().contextWindow,
                [conversationId]: usage.context_window,
              }
              usageUpdate.contextTokens = {
                ...get().contextTokens,
                [conversationId]: usage.context_tokens ?? null,
              }
            }
            set(usageUpdate)
            if (paused) {
              sawPausedDone = true
            } else {
              sawDone = true
            }
            break outer
          } else if (event.type === 'injected_message') {
            const d = event.data as { content: string; steer_id: string }
            // Flush batched stream mutations so the commit reads fully-applied
            // streamAgents, not a stale snapshot.
            flush()
            set((s) => ({
              lastAppliedEventId: nextEventId(s.lastAppliedEventId, event.event_id),
            }))
            get().__commitTurnAndInject(conversationId, d)
            continue
          }

          batchedSet((s) => applyStreamEvent(s, event))
          if (++processed % YIELD_EVERY === 0) {
            await yieldToEventLoop()
          }
        }
        break outer
      }
    } catch (err) {
      set((s) => ({
        error: (err as Error).message,
        isStreaming: false,
        pendingConfirmMap: {},
        pendingAsk: null,
        streamingConversationId: null,
        currentRunId: null,
        pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
      }))
      return
    } finally {
      flush()
      if (activeStreamController === controller) {
        activeStreamController = null
      }
    }

    if (sawDone || sawPausedDone) {
      const lastState = get()
      if (!lastState.error) {
        if (sawPausedDone) {
          await finalizePausedStream(get, set, conversationId)
        } else {
          await finalizeCompletedStream(get, set, conversationId)
        }
      }
    }
  },

  async steer(client, conversationId, content) {
    const text = content.trim()
    if (!text) return
    const state = get()
    if (!state.isStreaming || state.streamingConversationId !== conversationId) return

    // A sent-but-not-yet-injected steer is held in pendingSteers (rendered as a
    // chip above the input box), NOT in the transcript. cubepi injects it into
    // history at its next safe point and emits an injected_message SSE event,
    // at which point it gets committed into messages (handled elsewhere).
    // Streaming state is left untouched — the run keeps going. If the endpoint
    // reports the run was NOT steered (already finished / not in this process),
    // remove the pending entry.
    const steerId = nextMessageId('steer')
    set((s) => ({
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: [...(s.pendingSteers[conversationId] ?? []), { steerId, text }],
      },
    }))

    const removePending = () =>
      set((s) => ({
        pendingSteers: {
          ...s.pendingSteers,
          [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
            (p) => p.steerId !== steerId,
          ),
        },
      }))

    try {
      const res = await steerRun(client, conversationId, text, steerId)
      if (res.status === 'no_active_run') removePending()
    } catch (err) {
      console.error('Failed to steer run:', err)
      removePending()
    }
  },

  async cancelSteer(client, conversationId, steerId) {
    set((s) => ({
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
          (p) => p.steerId !== steerId,
        ),
      },
    }))
    try {
      await cancelSteer(client, conversationId, steerId)
    } catch (err) {
      console.error('Failed to cancel steer:', err)
    }
  },

  // Commit the in-flight assistant turn into history at the point cubepi
  // injected the steer, then append the steer user message right after, reset
  // the streaming buckets so subsequent deltas form a fresh bubble, and drop the
  // matching pending entry. This keeps the live transcript ordering identical to
  // a reload from the checkpointer (steer interleaved between assistant turns).
  __commitTurnAndInject(conversationId, data) {
    const state = get()
    // Idempotency: if this steer is already committed, no-op (replay-safe).
    const already = (state.messages[conversationId] ?? []).some(
      (m) => m.role === 'user' && m.metadata?.steer_id === data.steer_id,
    )
    if (already) return

    const { assistantMessage, toolMessages } = buildTurnMessages(
      state.streamAgents,
      state.toolResultMap,
      state.turnUsage[conversationId] ?? null,
    )
    const mainHasContent = !!assistantMessage && assistantMessage.content.length > 0

    const steerMessage: UserMessageType = {
      id: nextMessageId('user-steer'),
      role: 'user',
      content: [{ type: 'text', text: data.content }],
      timestamp: Date.now() / 1000,
      metadata: { steer_id: data.steer_id },
    }

    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [
          ...(s.messages[conversationId] ?? []),
          // Only the assistant bubble is gated on having content (avoid an empty
          // bubble when a steer drains before any text). Tool results always
          // commit — they must not be lost from the live view if a steer lands
          // right after a tool boundary.
          ...(mainHasContent ? [assistantMessage as AssistantMessageType] : []),
          ...toolMessages,
          steerMessage,
        ],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      pendingSteers: {
        ...s.pendingSteers,
        [conversationId]: (s.pendingSteers[conversationId] ?? []).filter(
          (p) => p.steerId !== data.steer_id,
        ),
      },
    }))
  },

  async cancelStream(client, conversationId) {
    const state = get()
    if (!state.isStreaming || state.streamingConversationId !== conversationId) return

    // Stop consuming the stream first so no late event mutates streamAgents
    // after we snapshot it below.
    activeStreamController?.abort()
    activeStreamController = null

    // Persist whatever was streamed so far as a cancelled assistant turn so
    // the partial content stays visible — otherwise it vanishes the moment
    // isStreaming flips false and only reappears after a reload (which reads
    // the checkpointer). Skip when nothing was produced to avoid an empty
    // bubble.
    const mainStream = get().streamAgents[MAIN_AGENT_KEY]
    const hasContent =
      !!mainStream &&
      (mainStream.blocks.length > 0 ||
        mainStream.text.length > 0 ||
        mainStream.thinking.length > 0 ||
        mainStream.toolResults.length > 0)

    if (hasContent) {
      await finalizeCompletedStream(get, set, conversationId, 'aborted')
    } else {
      set((s) => ({
        isStreaming: false,
        pendingConfirmMap: {},
        pendingAsk: null,
        streamingConversationId: null,
        currentRunId: null,
        statusPhase: null,
        pendingSteers: { ...s.pendingSteers, [conversationId]: [] },
      }))
    }

    try {
      await cancelActiveRun(client, conversationId)
    } catch (err) {
      console.error('Failed to cancel run:', err)
    }
  },

  clearStream() {
    set({
      streamAgents: {},
      pendingSteers: {},
      isStreaming: false,
      pendingConfirmMap: {},
      pendingAsk: null,
      streamingConversationId: null,
      currentRunId: null,
      lastAppliedEventId: null,
      statusPhase: null,
      todos: [],
      toolStartedMap: {},
      toolResultMap: {},
    })
  },

  clearLastRunStatus() {
    set({ lastRunStatus: null })
  },
  __applyEvent(event: AgentEvent) {
    set((s) => applyStreamEvent(s, event) as MessageStore)
  },
}))
