import { describe, it, expect } from 'vitest'
import { parseTestStream } from '../../src/api/providerTestStream'

function streamFromString(s: string): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(c) {
      c.enqueue(enc.encode(s))
      c.close()
    },
  })
}

describe('parseTestStream', () => {
  it('yields liveness, model, done', async () => {
    const text =
      'event: liveness\ndata: {"name":"liveness","status":"pass"}\n\n' +
      'event: model\ndata: {"model_db_id":"m1","overall":"pass","blocking_failed":false,"steps":[]}\n\n' +
      'event: done\ndata: {}\n\n'
    const out: string[] = []
    for await (const e of parseTestStream(streamFromString(text))) out.push(e.event)
    expect(out).toEqual(['liveness', 'model', 'done'])
  })
})
