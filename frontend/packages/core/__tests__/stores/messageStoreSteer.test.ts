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

  it('queues steering while the lifecycle is paused_hitl even though streaming is false', async () => {
    useMessageStore.setState({
      isStreaming: false,
      streamingConversationId: 'conv1',
      runLifecycle: { conv1: 'paused_hitl' },
    })
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      status: 'queued',
      run_id: 'r1',
      steer_id: 'ignored-by-store',
    })

    expect(await useMessageStore.getState().steer(fakeClient, 'conv1', 'after approval')).toBe(true)
    expect(useMessageStore.getState().pendingSteers.conv1[0]).toMatchObject({
      text: 'after approval',
      state: 'queued',
    })
  })

  it('does not recreate a pending chip when injected_message wins the POST race', async () => {
    let resolvePost:
      ((value: { status: 'queued'; run_id: string; steer_id: string }) => void) | null = null
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockImplementationOnce(
      (_client: unknown, _conversation: string, _text: string, steerId: string) =>
        new Promise((resolve) => {
          resolvePost = resolve
          queueMicrotask(() => {
            useMessageStore.getState().__commitTurnAndInject('conv1', {
              content: 'race text',
              steer_id: steerId,
            })
          })
        }),
    )

    const pending = useMessageStore.getState().steer(fakeClient, 'conv1', 'race text')
    await Promise.resolve()
    const injected = useMessageStore.getState().messages.conv1.at(-1)
    const steerId = injected?.metadata?.steer_id as string
    resolvePost?.({ status: 'queued', run_id: 'r1', steer_id: steerId })
    await pending

    expect(useMessageStore.getState().pendingSteers.conv1 ?? []).toHaveLength(0)
    expect(useMessageStore.getState().messages.conv1).toHaveLength(1)
  })

  it('refreshes history when an injected response arrives before its stream event', async () => {
    ;(steerRun as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      status: 'injected',
      run_id: 'r1',
      steer_id: 'server-steer-id',
    })
    const client = {
      resolvePath: (path: string) => path,
      get: vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            messages: [],
            active_run: null,
            pending_hitl: null,
            pending_steers: [],
            last_run_status: null,
          }),
      }),
    } as never

    expect(await useMessageStore.getState().steer(client, 'conv1', 'already injected')).toBe(true)

    expect(client.get).toHaveBeenCalledOnce()
  })
})
