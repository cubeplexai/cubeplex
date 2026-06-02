import type { UserEvent } from '../types'
import type { ApiClient } from './client'

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

/**
 * Streams user-scoped events from /api/v1/user/events.
 *
 * The endpoint is user-scoped (not workspace-scoped), so we bypass the client's
 * resolvePath to avoid accidental workspace injection.
 */
export async function* streamUserEvents(
  client: ApiClient,
  opts: { signal: AbortSignal; since?: string },
): AsyncGenerator<UserEvent> {
  const params = opts.since ? `?since=${encodeURIComponent(opts.since)}` : ''
  const url = `${client.baseUrl}/api/v1/user/events${params}`
  const res = await fetch(url, {
    method: 'GET',
    credentials: 'include',
    headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' },
    cache: 'no-store',
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`SSE connect failed: ${res.status}`)
  const reader = res.body?.getReader()
  if (!reader) return
  try {
    for await (const line of readLines(reader)) {
      if (!line.startsWith('data: ')) continue
      try {
        yield JSON.parse(line.slice(6)) as UserEvent
      } catch {
        // skip malformed lines
      }
    }
  } finally {
    reader.releaseLock()
  }
}
