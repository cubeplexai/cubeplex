import type { AgentEvent } from '../types'
import type { ApiClient } from './client'

async function* readLines(
  reader: ReadableStreamDefaultReader<Uint8Array>
): AsyncGenerator<string> {
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
  content: string
): AsyncGenerator<AgentEvent> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }
  if (client.workspaceId) headers['X-Workspace-Id'] = client.workspaceId
  const csrf = readCookie('cubebox_csrf')
  if (csrf) headers['X-CSRF-Token'] = csrf

  const res = await fetch(
    `${client.baseUrl}/api/v1/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      credentials: 'include',
      headers,
      cache: 'no-store',
      body: JSON.stringify({ content }),
    }
  )

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

  const reader = res.body!.getReader()
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
