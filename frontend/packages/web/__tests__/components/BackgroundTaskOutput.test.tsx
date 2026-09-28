import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'
import { useMessageStore, type BackgroundTask } from '@cubeplex/core'
import { BackgroundTaskDetails } from '@/components/chat/BackgroundTaskDetails'

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

const task = {
  id: 'bgt-1',
  tool_call_id: 'tc-1',
  state: 'succeeded',
  result_readiness: 'ready',
  details: { command_kind: 'execute', command: 'echo saved', exit_code: 0 },
} as BackgroundTask

function mount() {
  render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <BackgroundTaskDetails conversationId="conv-1" taskId="bgt-1" task={task} />
    </SWRConfig>,
  )
}

describe('Background output HTTP contract', () => {
  beforeEach(() => {
    useMessageStore.setState({ messages: {}, backgroundTasks: {}, streamingConversationId: null })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reads completed output once on expansion through the task-scoped endpoint', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ content: 'Final output' })))
    vi.stubGlobal('fetch', fetch)
    mount()
    await waitFor(() => expect(screen.getByText('Final output')).toBeVisible())
    expect(fetch).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/ws/ws-1/conversations/conv-1/background-tasks/bgt-1/output',
      { credentials: 'include' },
    )
  })

  it('suppresses retry when the server declares stale ready output unavailable', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Final output cannot be recovered' }), {
        status: 410,
      }),
    )
    vi.stubGlobal('fetch', fetch)
    mount()
    await waitFor(() => expect(screen.getByText('Final output cannot be recovered')).toBeVisible())
    expect(screen.getByText('outputUnavailable')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'retry' })).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledOnce()
  })
})
