// frontend/packages/core/src/types/message.ts
//
// Messages mirror cubepi's wire shape (cubepi/providers/base.py:Message). The
// backend returns `m.model_dump(mode="json")` directly; no cubebox-specific
// conversion layer.
//
// cubebox-specific data (attachments, memory snapshots, citations, subagent
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
  timestamp?: number | null // epoch seconds (cubepi convention)
  metadata?: Record<string, unknown> & {
    attachments?: MessageAttachment[]
    memory_snapshot?: unknown
    citations?: CitationData[]
    subagent_events?: SubagentSummary
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
  role: 'tool'
  tool_call_id: string
  tool_name: string
  content: ContentBlock[]
  is_error?: boolean
}

export type Message = UserMessage | AssistantMessage | ToolResultMessage

// --- Helpers (frontend ergonomics over the block-list shape) ---

export function getTextContent(msg: Message): string {
  return msg.content
    .filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text')
    .map((b) => b.text)
    .join('')
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
