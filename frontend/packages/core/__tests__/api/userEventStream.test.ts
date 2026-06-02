import { describe, it, expect, vi, afterEach } from 'vitest'
import { streamUserEvents } from '../../src/api/userEventStream'
import type { ApiClient } from '../../src/api/client'
import type { UserEvent } from '../../src/types'

function fakeClient(baseUrl = 'http://localhost:8011', notifyUnauthorized = vi.fn()): ApiClient {
  return { baseUrl, notifyUnauthorized } as unknown as ApiClient
}

function sseBody(lines: string): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(c) {
      c.enqueue(enc.encode(lines))
      c.close()
    },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('streamUserEvents', () => {
  it('yields parsed UserEvents from data: lines', async () => {
    const ev: UserEvent = {
      id: 'uev-1',
      type: 'memory_updated',
      workspace_id: 'ws-1',
      payload: {
        conversation_id: 'conv-1',
        run_id: 'run-1',
        items: [{ op: 'save', memory_id: 'mem-1' }],
      },
      created_at: '2026-06-02T10:00:00+00:00',
    }
    const body = `event: memory_updated\ndata: ${JSON.stringify(ev)}\n\n` + `: ping\n\n`

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        body: sseBody(body),
      }),
    )

    const ac = new AbortController()
    const results: UserEvent[] = []
    for await (const e of streamUserEvents(fakeClient(), { signal: ac.signal })) {
      results.push(e)
      ac.abort() // stop after first event
    }

    expect(results).toHaveLength(1)
    expect(results[0]).toEqual(ev)
  })

  it('skips ping lines and malformed data', async () => {
    const ev: UserEvent = {
      id: 'uev-2',
      type: 'memory_updated',
      workspace_id: null,
      payload: { conversation_id: 'conv-2', run_id: 'run-2', items: [] },
      created_at: '2026-06-02T10:00:00+00:00',
    }
    const body = `: ping\n\n` + `data: not-json\n\n` + `data: ${JSON.stringify(ev)}\n\n`

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        body: sseBody(body),
      }),
    )

    const ac = new AbortController()
    const results: UserEvent[] = []
    for await (const e of streamUserEvents(fakeClient(), { signal: ac.signal })) {
      results.push(e)
    }

    expect(results).toHaveLength(1)
    expect(results[0].id).toBe('uev-2')
  })

  it('appends since param when provided', async () => {
    let capturedUrl = ''
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        capturedUrl = url
        return Promise.resolve({ ok: true, body: sseBody('') })
      }),
    )

    const ac = new AbortController()
    for await (const _ of streamUserEvents(fakeClient(), { signal: ac.signal, since: 'uev-5' })) {
      break
    }

    expect(capturedUrl).toContain('since=uev-5')
  })

  it('throws on non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    const ac = new AbortController()
    await expect(async () => {
      for await (const _ of streamUserEvents(fakeClient(), { signal: ac.signal })) {
        // nothing
      }
    }).rejects.toThrow('SSE connect failed: 401')
  })

  it('fires notifyUnauthorized on 401 so auth redirect can run', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    const notify = vi.fn()
    const ac = new AbortController()
    await expect(async () => {
      for await (const _ of streamUserEvents(fakeClient('http://localhost:8011', notify), {
        signal: ac.signal,
      })) {
        // nothing
      }
    }).rejects.toThrow()
    expect(notify).toHaveBeenCalledOnce()
  })

  it('does NOT fire notifyUnauthorized on non-401 errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

    const notify = vi.fn()
    const ac = new AbortController()
    await expect(async () => {
      for await (const _ of streamUserEvents(fakeClient('http://localhost:8011', notify), {
        signal: ac.signal,
      })) {
        // nothing
      }
    }).rejects.toThrow()
    expect(notify).not.toHaveBeenCalled()
  })
})
