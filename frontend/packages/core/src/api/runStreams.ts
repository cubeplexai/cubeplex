import type { AgentEvent, Message, PendingHitl } from '../types'
import { toApiError, type ApiClient } from './client'
import { CSRF_COOKIE_NAME } from './cookieNames'

export interface ActiveRunBootstrap {
  run_id: string
  status: string
  user_message?: string | null
  last_event_id?: string | null
  // ISO timestamp recorded when the run was claimed in Redis. Used by the
  // frontend to tell the active run's user message apart from a prior turn
  // with identical content (any history user message older than this
  // belongs to a completed turn, not the active one).
  started_at?: string | null
  error_code?: string | null
  error_params?: Record<string, unknown> | null
  error_message?: string | null
}

export interface LastRunError {
  run_id: string
  error_code: string
  error_params?: Record<string, unknown> | null
  error_message: string
}

export interface ConversationBootstrap {
  messages: Message[]
  total: number
  active_run: ActiveRunBootstrap | null
  last_run_status: 'stale' | null
  last_run_error?: LastRunError | null
  usage_summary?: {
    turn?: {
      input_tokens: number
      output_tokens: number
      cache_read_tokens: number
      cache_write_tokens: number
    }
    session: {
      total_input_tokens: number
      total_output_tokens: number
      total_cache_read_tokens: number
      total_cache_write_tokens: number
    }
    context_window: number
    context_tokens?: number
  }
  /**
   * Cold-start fallback: when the Redis event stream has aged out but the
   * conversation has an unresolved HITL request, the backend serializes it
   * here so the UI can re-render the pending card without replaying SSE.
   * ``null`` when there is no pending HITL.
   */
  pending_hitl?: PendingHitl | null
}

export interface StartRunResponse {
  run_id: string
}

async function* readLines(reader: ReadableStreamDefaultReader<Uint8Array>): AsyncGenerator<string> {
  let buffer = ''
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      yield line
    }
  }
  if (buffer) yield buffer
}

function readCookie(name: string): string {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : ''
}

export async function getConversationBootstrap(
  client: ApiClient,
  conversationId: string,
): Promise<ConversationBootstrap> {
  const res = await client.get(`/api/v1/conversations/${conversationId}/bootstrap`)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<ConversationBootstrap>
}

export async function startMessageRun(
  client: ApiClient,
  conversationId: string,
  content: string,
  attachmentIds?: string[],
): Promise<StartRunResponse> {
  const body: { content: string; attachments?: string[] } = { content }
  if (attachmentIds && attachmentIds.length) body.attachments = attachmentIds
  const res = await client.post(`/api/v1/conversations/${conversationId}/messages`, body)
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<StartRunResponse>
}

export async function* streamRun(
  client: ApiClient,
  conversationId: string,
  runId: string,
  lastEventId?: string,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const csrf = readCookie(CSRF_COOKIE_NAME)
  if (csrf) headers['X-CSRF-Token'] = csrf

  const path = client.resolvePath(`/api/v1/conversations/${conversationId}/runs/${runId}/stream`)
  let res: Response
  try {
    res = await fetch(`${client.baseUrl}${path}`, {
      method: 'GET',
      credentials: 'include',
      headers,
      cache: 'no-store',
      signal,
    })
  } catch (err) {
    if ((err as Error).name === 'AbortError') return
    throw err
  }

  if (!res.ok || !res.body) {
    throw await toApiError(res)
  }

  const reader = res.body.getReader()
  try {
    for await (const line of readLines(reader)) {
      if (!line.startsWith('data: ')) continue
      try {
        yield JSON.parse(line.slice(6)) as AgentEvent
      } catch {
        // Ignore malformed lines and keep the stream alive.
      }
    }
  } catch (err) {
    if ((err as Error).name !== 'AbortError') throw err
  } finally {
    reader.releaseLock()
  }
}
