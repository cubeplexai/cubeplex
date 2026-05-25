import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    steerRun: vi.fn().mockResolvedValue({ steered: true, run_id: 'r1' }),
  }
})

import { steerRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

describe('messageStore.steer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: { conv1: [] },
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

  it('optimistically appends the user message and calls steerRun', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'go left instead')
    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(true)
    expect(state.streamingConversationId).toBe('conv1')
    const msgs = state.messages.conv1
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('user')
    expect(msgs[0].content).toEqual([{ type: 'text', text: 'go left instead' }])
    expect(steerRun).toHaveBeenCalledWith(fakeClient, 'conv1', 'go left instead')
  })

  it('is a no-op for empty content', async () => {
    await useMessageStore.getState().steer(fakeClient, 'conv1', '   ')
    expect(steerRun).not.toHaveBeenCalled()
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })

  it('does nothing when not streaming the given conversation', async () => {
    useMessageStore.setState({ isStreaming: false, streamingConversationId: null })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'hi')
    expect(steerRun).not.toHaveBeenCalled()
  })

  it('rolls back the optimistic bubble when the run was not steered', async () => {
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      steered: false,
      run_id: null,
    })
    await useMessageStore.getState().steer(fakeClient, 'conv1', 'too late')
    expect(useMessageStore.getState().messages.conv1).toHaveLength(0)
  })
})
