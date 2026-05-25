import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    steerRun: vi.fn().mockResolvedValue({ status: 'steered', run_id: 'r1' }),
  }
})

import { steerRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

describe('messageStore.steer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: { conv1: [] },
      pendingSteers: {},
      streamAgents: {
        main: {
          text: 'partial',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [],
          name: null,
        },
      },
      isStreaming: true,
      streamingConversationId: 'conv1',
      currentRunId: 'r1',
    })
  })

  it('adds the steer to pendingSteers (not messages) and calls steerRun', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'go left instead')
    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(true)
    expect(state.streamingConversationId).toBe('conv1')
    expect(state.messages.conv1).toHaveLength(0)
    const pending = state.pendingSteers.conv1
    expect(pending).toHaveLength(1)
    expect(pending[0].text).toBe('go left instead')
    expect(steerRun).toHaveBeenCalledWith(
      fakeClient,
      'conv1',
      'go left instead',
      pending[0].steerId,
    )
  })

  it('is a no-op for empty content', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', '   ')
    expect(steerRun).not.toHaveBeenCalled()
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
    expect(useMessageStore.getState().pendingSteers.conv1 ?? []).toHaveLength(0)
  })

  it('does nothing when not streaming the given conversation', async () => {
    useMessageStore.setState({ isStreaming: false, streamingConversationId: null })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'hi')
    expect(steerRun).not.toHaveBeenCalled()
    expect(useMessageStore.getState().pendingSteers.conv1 ?? []).toHaveLength(0)
  })

  it('removes the pending steer when the run was not steered', async () => {
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      status: 'no_active_run',
      run_id: null,
    })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'too late')
    expect(useMessageStore.getState().pendingSteers.conv1 ?? []).toHaveLength(0)
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })

  it('keeps the pending steer when status is published', async () => {
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      status: 'published',
      run_id: 'r1',
    })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'cross-instance steer')
    expect(useMessageStore.getState().pendingSteers.conv1).toHaveLength(1)
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })
})
