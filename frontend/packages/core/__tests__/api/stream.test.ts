import { afterEach, describe, it, expect, vi } from 'vitest'
import { cancelSteer, steerRun, streamMessages } from '../../src/api/stream'
function fakeClient(capture: { path?: string; body?: unknown } = {}) {
  return {
    baseUrl: '',
    resolvePath: (path: string) => path,
    post: vi.fn(async (path: string, body: unknown) => {
      capture.path = path
      capture.body = body
      return { ok: true, json: async () => ({ status: 'steered', run_id: 'r1' }) }
    }),
  } as never
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('steer api', () => {
  it('steerRun sends content + steer_id', async () => {
    const cap: { path?: string; body?: unknown } = {}
    await steerRun(fakeClient(cap), 'conv-1', 'do X', 's1')
    expect(cap.path).toBe('/api/v1/conversations/conv-1/steer')
    expect(cap.body).toEqual({ content: 'do X', steer_id: 's1' })
  })

  it('cancelSteer posts steer_id to the cancel route', async () => {
    const cap: { path?: string; body?: unknown } = {}
    await cancelSteer(fakeClient(cap), 'conv-1', 's1')
    expect(cap.path).toBe('/api/v1/conversations/conv-1/steer/cancel')
    expect(cap.body).toEqual({ steer_id: 's1' })
  })
})

describe('message stream errors', () => {
  it('classifies an active-run 409 for friendly composer recovery', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Conversation conv-1 already has an active run' }), {
          status: 409,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    )

    const events = []
    for await (const event of streamMessages(fakeClient(), 'conv-1', 'hello')) {
      events.push(event)
    }

    expect(events).toHaveLength(1)
    expect(events[0].data).toMatchObject({
      error_code: 'active_run_conflict',
      params: { http_status: 409 },
    })
  })

  it('does not invent a Connection lost run error when fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(async () => {
      for await (const _event of streamMessages(fakeClient(), 'conv-1', 'hello')) {
        void _event
      }
    }).rejects.toThrow('Failed to fetch')
  })
})
