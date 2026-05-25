import { describe, it, expect, vi } from 'vitest'
import { steerRun, cancelSteer } from '../../src/api/stream'

function fakeClient(capture: { path?: string; body?: unknown }) {
  return {
    post: vi.fn(async (path: string, body: unknown) => {
      capture.path = path
      capture.body = body
      return { ok: true, json: async () => ({ status: 'steered', run_id: 'r1' }) }
    }),
  } as never
}

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
