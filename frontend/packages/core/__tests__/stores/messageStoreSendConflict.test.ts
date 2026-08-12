import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentEvent } from '../../src/types'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return { ...actual, streamMessages: vi.fn() }
})

import { ApiError, streamMessages } from '../../src/api'
import { useMessageStore } from '../../src/stores/messageStore'

const fakeClient = { resolvePath: (path: string) => path } as never

function activeRunConflict(): AsyncGenerator<AgentEvent> {
  return (async function* () {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: {
        error_code: 'active_run_conflict',
        message: 'Previous turn is still finishing.',
      },
      agent_id: null,
      agent_name: null,
    } as AgentEvent
  })()
}

describe('messageStore.send active-run conflict', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(streamMessages).mockImplementation(activeRunConflict)
    useMessageStore.setState({
      messages: { conv1: [] },
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
      errors: {},
    })
  })

  it('rejects with a typed conflict and removes the unaccepted optimistic message', async () => {
    await expect(
      useMessageStore.getState().send(fakeClient, 'conv1', 'keep my draft'),
    ).rejects.toEqual(
      expect.objectContaining<ApiError>({ status: 409, code: 'active_run_conflict' }),
    )

    expect(useMessageStore.getState().messages.conv1).toEqual([])
    expect(useMessageStore.getState().errors.conv1 ?? null).toBeNull()
  })
})
