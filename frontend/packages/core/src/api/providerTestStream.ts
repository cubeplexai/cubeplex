import type { ApiClient } from './client'
import { toApiError } from './client'

export interface TestStreamEvent {
  event: 'liveness' | 'model' | 'done'
  data: unknown
}

export async function* parseTestStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<TestStreamEvent> {
  const reader = stream.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let curEvent = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trimEnd()
      buf = buf.slice(i + 1)
      if (line.startsWith('event: ')) {
        curEvent = line.slice(7)
      } else if (line.startsWith('data: ')) {
        yield { event: curEvent as TestStreamEvent['event'], data: JSON.parse(line.slice(6)) }
      }
    }
  }
}

export async function startTestStream(
  client: ApiClient,
  providerId: string,
  modelDbIds: string[],
): Promise<ReadableStream<Uint8Array>> {
  const res = await client.postRaw(
    `/api/v1/admin/providers/${providerId}/test/stream`,
    { model_db_ids: modelDbIds },
    { Accept: 'text/event-stream' },
  )
  if (!res.ok || !res.body) throw await toApiError(res)
  return res.body
}
