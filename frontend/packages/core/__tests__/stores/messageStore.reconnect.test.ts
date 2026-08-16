import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentEvent } from '../../src/types'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    streamMessages: vi.fn(),
    streamRun: vi.fn(() =>
      (async function* () {
        yield* []
      })(),
    ),
  }
})

import { streamMessages, streamRun } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'
import { getTextContent } from '../../src/types'

const CONV = 'conv-reconnect'
const fakeClient = { resolvePath: (path: string) => path } as never

function delta(content: string, runId = 'run-1'): AgentEvent {
  return {
    type: 'text_delta',
    timestamp: new Date().toISOString(),
    data: { content },
    agent_id: null,
    agent_name: null,
    run_id: runId,
  }
}

function doneEvent(): AgentEvent {
  return {
    type: 'done',
    timestamp: new Date().toISOString(),
    data: {},
    agent_id: null,
    agent_name: null,
  }
}

function errorEvent(message: string): AgentEvent {
  return {
    type: 'error',
    timestamp: new Date().toISOString(),
    data: { error_code: 'internal_error', message },
    agent_id: null,
    agent_name: null,
  }
}

describe('messageStore transport drop', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.getState().clearStream()
    useMessageStore.setState({
      messages: { [CONV]: [] },
      errors: {},
      runLifecycle: {},
      streamConnection: null,
    })
  })

  it('reattaches via streamRun after the POST stream ends mid-run', async () => {
    vi.mocked(streamMessages).mockImplementation(async function* (_c, _id, _content, _a, _s, opts) {
      opts?.onRunId?.('run-1')
      yield delta('Hi')
    })
    vi.mocked(streamRun).mockImplementation(async function* () {
      yield delta(' there')
      yield doneEvent()
    })

    await useMessageStore.getState().send(fakeClient, CONV, 'hello')

    expect(streamRun).toHaveBeenCalled()
    const msgs = useMessageStore.getState().messages[CONV] ?? []
    const assistant = msgs.find((m) => m.role === 'assistant')
    expect(assistant && getTextContent(assistant)).toBe('Hi there')
    expect(useMessageStore.getState().isStreaming).toBe(false)
    expect(useMessageStore.getState().errors[CONV] ?? null).toBeNull()
    expect(useMessageStore.getState().streamConnection).toBeNull()
  })

  it('does not mark a pre-run-id drop as a model failure', async () => {
    vi.mocked(streamMessages).mockImplementation(async function* () {
      yield* []
      throw new Error('Failed to fetch')
    })

    await useMessageStore.getState().send(fakeClient, CONV, 'hello')

    const state = useMessageStore.getState()
    expect(state.errors[CONV] ?? null).toBeNull()
    expect(state.isStreaming).toBe(false)
    expect(state.streamConnection).toBeNull()
    const msgs = state.messages[CONV] ?? []
    expect(msgs.every((m) => m.role === 'user')).toBe(true)
    expect(msgs.some((m) => m.role === 'assistant')).toBe(false)
  })

  it('ignores a server error event while the user is cancelling', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.mocked(streamMessages).mockImplementation(async function* (_c, _id, _content, _a, _s, opts) {
      opts?.onRunId?.('run-1')
      yield delta('partial')
      await held
      yield errorEvent('Run cancelled')
    })

    const sendPromise = useMessageStore.getState().send(fakeClient, CONV, 'hello')
    await Promise.resolve()
    useMessageStore.setState({
      cancellingConversationIds: { [CONV]: true },
    })
    release()
    await sendPromise

    const state = useMessageStore.getState()
    const assistant = (state.messages[CONV] ?? []).find((m) => m.role === 'assistant')
    expect(assistant?.stop_reason).not.toBe('error')
    expect(state.errors[CONV] ?? null).toBeNull()
  })
})
