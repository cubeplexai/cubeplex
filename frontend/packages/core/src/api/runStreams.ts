import type { AgentEvent, Message } from '../types'
import { toApiError, type ApiClient } from './client'

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
}

export interface ConversationBootstrap {
  messages: Message[]
  total: number
  active_run: ActiveRunBootstrap | null
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
): Promise<StartRunResponse> {
  const res = await client.post(`/api/v1/conversations/${conversationId}/messages`, { content })
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<StartRunResponse>
}

export async function* streamRun(
  client: ApiClient,
  conversationId: string,
  runId: string,
  lastEventId?: string,
): AsyncGenerator<AgentEvent> {
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }
  if (lastEventId) headers['Last-Event-ID'] = lastEventId
  const csrf = readCookie('cubebox_csrf')
  if (csrf) headers['X-CSRF-Token'] = csrf

  const path = client.resolvePath(`/api/v1/conversations/${conversationId}/runs/${runId}/stream`)
  const res = await fetch(`${client.baseUrl}${path}`, {
    method: 'GET',
    credentials: 'include',
    headers,
    cache: 'no-store',
  })

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
  } finally {
    reader.releaseLock()
  }
}
