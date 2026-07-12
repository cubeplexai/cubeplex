// frontend/packages/core/src/types/message.ts
//
// Messages mirror cubepi's wire shape (cubepi/providers/base.py:Message). The
// backend returns `m.model_dump(mode="json")` directly; no cubeplex-specific
// conversion layer.
//
// cubeplex-specific data (attachments, memory snapshots, citations, subagent
// payloads) lives inside `metadata` — cubepi treats metadata as opaque and
// round-trips it through the checkpointer unchanged.
import type { CitationData } from './citation'
import type { ContentBlock } from './events'

export interface SubagentToolResult {
  tool_name: string
  tool_call_id: string
  content: string
  content_type?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface SubagentSummary {
  text: string
  tool_calls: {
    name: string
    arguments: Record<string, unknown>
    id?: string
    started_at?: string | null
  }[]
  tool_results?: SubagentToolResult[]
  thinking: string
  role?: string
  task?: string
}

export interface MessageAttachment {
  // Persisted as `file_id` in cubepi UserMessage.metadata.attachments.
  file_id: string
  filename: string
  kind: 'image' | 'document' | 'other'
  size_bytes: number
  width?: number | null
  height?: number | null
  thumbnail_url?: string | null
  download_url?: string | null
}

// Usage matches cubepi.providers.base.Usage.
export interface MessageUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens?: number
  cache_write_tokens?: number
}

interface MessageBase {
  // Synthesized client-side for React keys; never sent to the backend.
  id: string
  // Server-side cubepi_messages.seq for this row. Stable across reloads and
  // tail/backscroll pagination — drives the ``#msg-<seq>`` DOM anchor that
  // conversation-search deep-links into. Absent on in-memory messages that
  // have not yet been persisted (e.g. the optimistic user bubble before the
  // run claims it).
  seq?: number
  timestamp?: number | null // epoch seconds (cubepi convention)
  // Identifies the agent run this message belongs to. User + assistant
  // messages produced within the same turn share a run_id. Null on
  // very-old rows (pre-cubepi v3) and on framework-injected synthetic
  // messages that never enter a run. The forkConversation API uses this
  // as ``after_run_id``.
  run_id?: string | null
  metadata?: Record<string, unknown> & {
    attachments?: MessageAttachment[]
    memory_snapshot?: unknown
    citations?: CitationData[]
    subagent_events?: SubagentSummary
    // Set on a steer user message committed mid-run; used for replay idempotency.
    steer_id?: string
    // Framework-injected user-role message (cubepi synthetic_user_message):
    // model-facing scaffolding like todo-guard nudges or goal continuations.
    // Never rendered as a user bubble; synthetic_source is trace-only.
    synthetic?: boolean
    synthetic_source?: string
    // Stamped on every user message (1:1 included) so a 1:1→group conversion
    // can attribute past messages. The SenderBadge is gated on the conversation
    // being a group chat (is_group_chat), not on the mere presence of these.
    sender_user_id?: string
    sender_display_name?: string
  }
}

export interface UserMessage extends MessageBase {
  role: 'user'
  content: ContentBlock[]
}

export interface AssistantMessage extends MessageBase {
  role: 'assistant'
  content: ContentBlock[]
  stop_reason?: string
  error_message?: string | null
  usage?: MessageUsage | null
  provider_id?: string
  model_id?: string
  response_id?: string | null
}

export interface ToolResultMessage extends MessageBase {
  // Mirrors cubepi.ToolResultMessage.role (= "tool_result"). Not "tool".
  role: 'tool_result'
  tool_call_id: string
  tool_name: string
  content: ContentBlock[]
  is_error?: boolean
  // cubepi.ToolResultMessage.details: middleware-attached payload (e.g. the
  // raw SSE event array a subagent tool result carries). Shape is per-tool.
  details?: unknown
}

export type Message = UserMessage | AssistantMessage | ToolResultMessage

// --- Helpers (frontend ergonomics over the block-list shape) ---

export function getTextContent(msg: Message): string {
  return msg.content
    .filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text')
    .map((b) => b.text)
    .join('')
}

/**
 * Content to feed tool-result previews (SearchResultView / WebFetchView, citation
 * popovers). CitationMiddleware rewrites a tool result's `.content` to 【N-M】-marked
 * chunk text for the LLM and stashes the raw, parseable output in
 * `details.original_content` (see backend cubeplex/middleware/citation.py). Previews
 * need that raw output — falling back to `.content` would feed them the citation
 * markup, which they can't parse. The live SSE path already prefers original_content
 * (cubeplex/agents/stream.py `_stringify_tool_result`); this keeps reload consistent.
 */
export function getToolResultPreviewContent(msg: ToolResultMessage): string {
  const details = msg.details as { original_content?: unknown } | null | undefined
  if (typeof details?.original_content === 'string') return details.original_content
  return getTextContent(msg)
}

export function getThinking(msg: AssistantMessage): string {
  return msg.content
    .filter((b): b is Extract<ContentBlock, { type: 'thinking' }> => b.type === 'thinking')
    .map((b) => b.thinking)
    .join('')
}

export function getToolCalls(
  msg: AssistantMessage,
): Extract<ContentBlock, { type: 'tool_call' }>[] {
  return msg.content.filter(
    (b): b is Extract<ContentBlock, { type: 'tool_call' }> => b.type === 'tool_call',
  )
}

/**
 * Extract a SubagentSummary for a tool result message, handling both shapes:
 *
 *   - in-memory after live finalization: `metadata.subagent_events` already
 *     holds a normalized `SubagentSummary`
 *   - reloaded from cubepi: `details.subagent_events` holds the raw SSE event
 *     list collected by `SubAgentMiddleware` — we replay it into a summary
 *
 * Returns null when neither shape is present.
 */
export function getSubagentSummary(msg: ToolResultMessage): SubagentSummary | null {
  const fromMeta = msg.metadata?.subagent_events
  if (fromMeta && !Array.isArray(fromMeta)) return fromMeta as SubagentSummary

  const details = msg.details as { subagent_events?: unknown } | null | undefined
  const events = details?.subagent_events
  if (!Array.isArray(events)) return null

  const summary: SubagentSummary = {
    text: '',
    tool_calls: [],
    tool_results: [],
    thinking: '',
  }
  for (const evt of events) {
    if (!evt || typeof evt !== 'object') continue
    const e = evt as Record<string, unknown>
    switch (e.type) {
      case 'text_delta':
        summary.text += typeof e.delta === 'string' ? e.delta : ''
        break
      case 'reasoning':
        summary.thinking += typeof e.delta === 'string' ? e.delta : ''
        break
      case 'tool_call':
        summary.tool_calls.push({
          id: typeof e.id === 'string' ? e.id : undefined,
          name: typeof e.name === 'string' ? e.name : '',
          arguments: (e.arguments as Record<string, unknown>) ?? {},
        })
        break
      case 'tool_result':
        summary.tool_results!.push({
          tool_name: typeof e.name === 'string' ? e.name : '',
          tool_call_id: typeof e.tool_call_id === 'string' ? e.tool_call_id : '',
          content: typeof e.result === 'string' ? e.result : String(e.result ?? ''),
        })
        break
    }
  }
  return summary
}
