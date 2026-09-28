import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  BackgroundTaskEventItem,
  BackgroundTaskEventsLoadMore,
} from '@/components/chat/BackgroundTaskEvents'

const mocks = vi.hoisted(() => ({
  events: [
    {
      id: 'event-1',
      task_id: 'task-1',
      state: 'delivered' as const,
      summary: 'Build finished',
      result_ref: 'artifact://report',
      created_at: '2026-09-22T01:00:00Z',
      updated_at: '2026-09-22T01:01:00Z',
      revision: 2,
    },
  ],
  tasks: [
    {
      id: 'task-1',
      tool_call_id: 'tool-1',
      details: {
        command_kind: 'execute',
        command: 'pnpm build',
        log_path: '/workspace/build.log',
        exit_code: 0,
      },
      result_readiness: 'ready',
    },
  ],
  hasMore: true,
  loadMore: vi.fn(),
  setWorkspaceId: vi.fn(),
  toastError: vi.fn(),
}))

vi.mock('@cubeplex/core', () => ({
  getBackgroundTask: vi.fn(),
  createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
  useMessageStore: (
    selector: (state: {
      backgroundEvents: Record<string, typeof mocks.events>
      backgroundEventsHasMore: Record<string, boolean>
      loadMoreBackgroundEvents: typeof mocks.loadMore
    }) => unknown,
  ) =>
    selector({
      backgroundTasks: { 'conv-1': mocks.tasks },
      messages: {},
      backgroundEvents: { 'conv-1': mocks.events },
      backgroundEventsHasMore: { 'conv-1': mocks.hasMore },
      loadMoreBackgroundEvents: mocks.loadMore,
    }),
}))
vi.mock('@/hooks/useSandboxFileContent', () => ({
  useSandboxFileContent: () => ({ content: 'Compiled successfully', loading: false, error: null }),
}))
vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    ({
      results: 'Background results',
      resultLabel: 'Background result',
      'events.delivered': 'Delivered',
      loadOlderResults: 'Load earlier background results',
      loadMoreFailed: 'Could not load earlier background results. Try again.',
    })[key] ?? key,
}))
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }))

describe('BackgroundTaskEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.hasMore = true
    mocks.loadMore.mockResolvedValue(undefined)
  })

  it('renders a compact system result and loads older results', async () => {
    render(
      <>
        <BackgroundTaskEventItem conversationId="conv-1" event={mocks.events[0] as never} />
        <BackgroundTaskEventsLoadMore conversationId="conv-1" />
      </>,
    )

    expect(screen.getByText('Build finished')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Load earlier background results' }))
    await waitFor(() => expect(mocks.loadMore).toHaveBeenCalledOnce())
    expect(mocks.loadMore).toHaveBeenCalledWith(expect.anything(), 'conv-1')
  })

  it('opens the originating command and its output from a result notification', async () => {
    render(<BackgroundTaskEventItem conversationId="conv-1" event={mocks.events[0] as never} />)
    fireEvent.click(screen.getByText('Background result'))
    await waitFor(() => expect(screen.getByText(/pnpm build/)).toBeVisible())
    expect(screen.getByText('Compiled successfully')).toBeVisible()
    expect(screen.queryByText('artifact://report')).not.toBeInTheDocument()
  })

  it('reports pagination failures without removing the current result', async () => {
    mocks.loadMore.mockRejectedValue(new Error('network down'))
    render(
      <>
        <BackgroundTaskEventItem conversationId="conv-1" event={mocks.events[0] as never} />
        <BackgroundTaskEventsLoadMore conversationId="conv-1" />
      </>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Load earlier background results' }))

    await waitFor(() =>
      expect(mocks.toastError).toHaveBeenCalledWith(
        'Could not load earlier background results. Try again.',
      ),
    )
    expect(screen.getByText('Build finished')).toBeInTheDocument()
  })
})
