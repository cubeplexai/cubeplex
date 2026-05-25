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
})
