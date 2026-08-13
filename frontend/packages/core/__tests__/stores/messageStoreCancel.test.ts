import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentStream } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    cancelActiveRun: vi.fn().mockResolvedValue({ status: 'cancelled', run_id: 'r1' }),
    getConversationBootstrap: vi.fn(),
  }
})

import { cancelActiveRun, getConversationBootstrap } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

function seedStreaming(conversationId: string, stream: Partial<AgentStream>): void {
  useMessageStore.setState({
    messages: { [conversationId]: [] },
    streamAgents: {
      main: {
        text: '',
        toolCalls: [],
        toolResults: [],
        thinking: '',
        blocks: [],
        name: null,
        ...stream,
      },
    },
    isStreaming: true,
    streamingConversationId: conversationId,
    currentRunId: 'r1',
  })
}

function idleBootstrap() {
  return {
    messages: [],
    oldest_seq: null,
    has_more: false,
    todos: null,
    active_run: null,
    pending_hitl: null,
    last_run_status: null,
  }
}

describe('messageStore.cancelStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getConversationBootstrap).mockResolvedValue(idleBootstrap())
    useMessageStore.setState({
      messages: {},
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
      pendingConfirmMap: {},
      pendingAsk: null,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('finalizes partial streamed content into a cancelled assistant message', async () => {
    seedStreaming('conv1', {
      text: 'partial answer',
      blocks: [{ type: 'text', text: 'partial answer' }],
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({
      ...idleBootstrap(),
      messages: [
        {
          id: 'cancelled-assistant-1',
          role: 'assistant',
          content: [{ type: 'text', text: 'partial answer' }],
          stop_reason: 'aborted',
        },
      ],
    })

    await useMessageStore.getState().cancelStream(fakeClient, 'conv1')

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(false)
    const msgs = state.messages.conv1
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('assistant')
    expect(msgs[0].stop_reason).toBe('aborted')
    expect(msgs[0].content).toEqual([{ type: 'text', text: 'partial answer' }])
    expect(cancelActiveRun).toHaveBeenCalledOnce()
  })

  it('does not append an empty bubble when nothing was streamed', async () => {
    seedStreaming('conv1', {})

    await useMessageStore.getState().cancelStream(fakeClient, 'conv1')

    const state = useMessageStore.getState()
    expect(state.isStreaming).toBe(false)
    expect(state.messages.conv1 ?? []).toHaveLength(0)
    expect(cancelActiveRun).toHaveBeenCalledOnce()
  })

  it('removes a dispatched chip that reached history just before cancellation', async () => {
    seedStreaming('conv1', {})
    useMessageStore.setState({
      pendingSteers: {
        conv1: [
          {
            steerId: 'steer-before-cancel',
            text: 'already injected',
            state: 'dispatched',
            createdAt: '2026-08-12T00:00:00.000Z',
          },
        ],
      },
    })
    vi.mocked(getConversationBootstrap).mockResolvedValue({
      ...idleBootstrap(),
      messages: [
        {
          id: 'steer-message-1',
          role: 'user',
          content: [{ type: 'text', text: 'already injected' }],
          metadata: { steer_id: 'steer-before-cancel' },
        },
      ],
    })

    await useMessageStore.getState().cancelStream(fakeClient, 'conv1')

    expect(useMessageStore.getState().pendingSteers.conv1).toEqual([])
  })

  it('keeps recovery locked and retries when post-stop reconciliation fails', async () => {
    vi.useFakeTimers()
    seedStreaming('conv1', {})
    useMessageStore.setState({
      pendingSteers: {
        conv1: [
          {
            steerId: 'steer-retry-refresh',
            text: 'already injected',
            state: 'dispatched',
            createdAt: '2026-08-12T00:00:00.000Z',
          },
        ],
      },
    })
    vi.mocked(getConversationBootstrap)
      .mockResolvedValueOnce(idleBootstrap())
      .mockRejectedValueOnce(new Error('bootstrap unavailable'))
      .mockResolvedValue({
        ...idleBootstrap(),
        messages: [
          {
            id: 'steer-message-retry',
            role: 'user',
            content: [{ type: 'text', text: 'already injected' }],
            metadata: { steer_id: 'steer-retry-refresh' },
          },
        ],
      })

    let settled = false
    const cancelling = useMessageStore
      .getState()
      .cancelStream(fakeClient, 'conv1')
      .finally(() => {
        settled = true
      })
    await vi.waitFor(() => expect(getConversationBootstrap).toHaveBeenCalledTimes(2))

    expect(settled).toBe(false)
    expect(useMessageStore.getState().cancellingConversationIds).toEqual({ conv1: true })
    expect(useMessageStore.getState().pendingSteers.conv1[0].state).toBe('dispatched')

    await vi.advanceTimersByTimeAsync(500)
    await cancelling

    expect(getConversationBootstrap).toHaveBeenCalledTimes(3)
    expect(useMessageStore.getState().pendingSteers.conv1).toEqual([])
    expect(useMessageStore.getState().cancellingConversationIds).toEqual({})
  })

  it('is a no-op when not streaming the given conversation', async () => {
    await useMessageStore.getState().cancelStream(fakeClient, 'conv-other')
    expect(cancelActiveRun).not.toHaveBeenCalled()
  })

  it('does not clear a replacement conversation after cancellation returns', async () => {
    let resolveCancel: (() => void) | undefined
    vi.mocked(cancelActiveRun).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCancel = () => resolve({ status: 'cancelled', run_id: 'r1' })
      }),
    )
    seedStreaming('conv-a', {})

    const cancelling = useMessageStore.getState().cancelStream(fakeClient, 'conv-a')
    await Promise.resolve()
    seedStreaming('conv-b', {
      text: 'keep B alive',
      blocks: [{ type: 'text', text: 'keep B alive' }],
    })
    resolveCancel?.()
    await cancelling

    expect(useMessageStore.getState()).toMatchObject({
      isStreaming: true,
      streamingConversationId: 'conv-b',
      currentRunId: 'r1',
      streamAgents: { main: { text: 'keep B alive' } },
    })
  })

  it('keeps a paused HITL conversation cancelling until bootstrap reports it idle', async () => {
    vi.useFakeTimers()
    useMessageStore.setState({
      isStreaming: false,
      streamingConversationId: 'conv1',
      currentRunId: 'r1',
      pendingAsk: {
        question_id: 'q1',
        questions: [],
        timeout_seconds: null,
        requestedAt: Date.now(),
        run_id: 'r1',
      },
    })
    vi.mocked(getConversationBootstrap)
      .mockResolvedValueOnce({
        ...idleBootstrap(),
        active_run: { run_id: 'r1', status: 'running' },
      })
      .mockResolvedValue(idleBootstrap())

    const cancelling = useMessageStore.getState().cancelStream(fakeClient, 'conv1')
    await Promise.resolve()

    expect(cancelActiveRun).toHaveBeenCalledOnce()
    expect(useMessageStore.getState()).toMatchObject({
      cancellingConversationIds: { conv1: true },
    })

    await vi.advanceTimersByTimeAsync(5_000)
    await cancelling

    expect(useMessageStore.getState()).toMatchObject({ cancellingConversationIds: {} })
  })
})
