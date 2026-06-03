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
  if (res.status === 401) {
    // Session expired or cookie cleared in another tab. Fire the same
    // unauthorized handlers ApiClient.doFetch uses so useAuthRedirect can
    // bounce the user to /login instead of retrying forever in the
    // useUserEvents backoff loop.
    client.notifyUnauthorized()
  }
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

/**
 * Mark a user event read (acknowledged).
 *
 * In the current design, user events are consumed as "refresh now" triggers
 * by the chip (`useUserEvents` updates the store; the chip refetches its
 * count). Once the client has applied the trigger, the event no longer needs
 * to be replayed on the next reconnect — without acking it, every fresh SSE
 * connection re-streams the entire `read_at IS NULL` backlog and grows the
 * client store unboundedly over time.
 *
 * User-scoped endpoint — bypass `resolvePath` like `streamUserEvents`.
 *
 * Best-effort: a failed ack just means the event will be re-delivered on the
 * next reconnect; the store's id-based dedup handles the resulting duplicate.
 */
export async function markUserEventRead(client: ApiClient, eventId: string): Promise<void> {
  // POST direct via fetch — same reason as streamUserEvents above. Uses the
  // existing /api/v1/user/events/{id}/read endpoint added in v2.
  const url = `${client.baseUrl}/api/v1/user/events/${encodeURIComponent(eventId)}/read`
  await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  })
  // No error inspection — fire-and-forget. Network blip / 5xx is fine, event
  // gets re-delivered next reconnect and the store dedupes.
}
