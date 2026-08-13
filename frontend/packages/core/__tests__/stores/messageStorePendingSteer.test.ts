import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (orig) => {
  const actual = await (orig as () => Promise<Record<string, unknown>>)()
  return {
    ...actual,
    steerRun: vi.fn(async () => ({ status: 'steered', run_id: 'r1' })),
    cancelSteer: vi.fn(async () => ({ status: 'cancelled', run_id: 'r1' })),
  }
})

const client = {} as never

describe('pending steers', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: {},
      pendingSteers: {},
      isStreaming: true,
      streamingConversationId: 'c1',
    })
  })

  it('steer() adds to pendingSteers, not messages', async () => {
    await useMessageStore.getState().steer(client, 'c1', 'do X')
    const s = useMessageStore.getState()
    expect(s.pendingSteers.c1).toHaveLength(1)
    expect(s.pendingSteers.c1[0].text).toBe('do X')
    expect(s.messages.c1 ?? []).toHaveLength(0)
  })

  it('cancelSteer() removes the pending entry', async () => {
    await useMessageStore.getState().steer(client, 'c1', 'do X')
    const id = useMessageStore.getState().pendingSteers.c1[0].steerId
    await useMessageStore.getState().cancelSteer(client, 'c1', id)
    expect(useMessageStore.getState().pendingSteers.c1 ?? []).toHaveLength(0)
  })

  it('reloads the authoritative chip when cancelSteer() fails', async () => {
    const { cancelSteer } = await import('../../src/api')
    vi.mocked(cancelSteer).mockRejectedValueOnce(new Error('network down'))
    const recoveryClient = {
      resolvePath: (path: string) => path,
      get: vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            messages: [],
            active_run: null,
            pending_hitl: null,
            pending_steers: [
              {
                steer_id: 'server-steer',
                content: 'still queued',
                state: 'queued',
                created_at: '2026-08-13T00:00:00Z',
              },
            ],
            last_run_status: null,
          }),
      }),
    } as never
    useMessageStore.setState({
      pendingSteers: {
        c1: [
          {
            steerId: 'server-steer',
            text: 'still queued',
            state: 'queued',
            createdAt: '2026-08-13T00:00:00Z',
          },
        ],
      },
    })

    const cancelled = await useMessageStore
      .getState()
      .cancelSteer(recoveryClient, 'c1', 'server-steer')

    expect(cancelled).toBe(false)
    expect(useMessageStore.getState().pendingSteers.c1).toEqual([
      expect.objectContaining({ steerId: 'server-steer', state: 'queued' }),
    ])
  })
})
