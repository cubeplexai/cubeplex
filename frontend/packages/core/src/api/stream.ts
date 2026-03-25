import type { AgentEvent } from '../types'

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

export async function* streamMessages(
  baseUrl: string,
  conversationId: string,
  content: string
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${baseUrl}/api/v1/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })

  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`)
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: error.message },
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
  } catch (err) {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: 'Connection lost' },
    } as AgentEvent
  }
}
