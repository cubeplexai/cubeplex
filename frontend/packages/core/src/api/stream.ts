import type { AgentEvent } from '../types'
import type { ApiClient } from './client'
import { streamRun } from './runStreams'

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

export async function* streamMessages(
  client: ApiClient,
  conversationId: string,
  content: string,
  attachmentIds?: string[],
): AsyncGenerator<AgentEvent> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }
  const csrf = readCookie('cubebox_csrf')
  if (csrf) headers['X-CSRF-Token'] = csrf

  const path = client.resolvePath(`/api/v1/conversations/${conversationId}/messages`)
  const requestBody: { content: string; attachments?: string[] } = { content }
  if (attachmentIds && attachmentIds.length) requestBody.attachments = attachmentIds
  try {
    const res = await fetch(`${client.baseUrl}${path}`, {
      method: 'POST',
      credentials: 'include',
      headers,
      cache: 'no-store',
      body: JSON.stringify(requestBody),
    })

    if (!res.ok) {
      yield {
        type: 'error',
        timestamp: new Date().toISOString(),
        data: { message: `HTTP ${res.status}` },
        agent_id: null,
        agent_name: null,
      } as AgentEvent
      return
    }

    const contentType = res.headers.get('content-type') ?? ''
    if (contentType.includes('text/event-stream')) {
      const reader = res.body?.getReader()
      if (!reader) return
      try {
        for await (const line of readLines(reader)) {
          if (line.startsWith('data: ')) {
            try {
              yield JSON.parse(line.slice(6)) as AgentEvent
            } catch {
              // skip malformed lines
            }
          }
        }
      } finally {
        reader.releaseLock()
      }
      return
    }

    const body = (await res.json()) as { run_id: string }
    for await (const event of streamRun(client, conversationId, body.run_id)) {
      yield event
    }
  } catch {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: 'Connection lost' },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
  }
}
