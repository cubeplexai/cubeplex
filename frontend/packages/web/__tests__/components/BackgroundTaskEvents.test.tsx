import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { usePanelStore } from '@cubeplex/core'
import { BackgroundTaskResultPanel } from '@/components/panel/BackgroundTaskResultPanel'

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

vi.mock('@cubeplex/core', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@cubeplex/core')>()),
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
vi.mock('@/hooks/useBackgroundTaskOutput', () => ({
  backgroundTaskOutputUrl: () => '/task-output',
  useBackgroundTaskOutput: () => ({
    content: 'Compiled successfully',
    loading: false,
    error: null,
  }),
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

function PreviewHost() {
  const view = usePanelStore((state) => state.view)
  if (view.type !== 'background-task-result') return null
  return (
    <aside aria-label="Result preview">
      <BackgroundTaskResultPanel
        key={view.event.id}
        conversationId={view.conversationId}
        event={view.event}
      />
    </aside>
  )
}

describe('BackgroundTaskEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    usePanelStore.getState().close()
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

    expect(screen.getByText('Background result')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Load earlier background results' }))
    await waitFor(() => expect(mocks.loadMore).toHaveBeenCalledOnce())
    expect(mocks.loadMore).toHaveBeenCalledWith(expect.anything(), 'conv-1')
  })

  it('opens result details in the shared preview slot without expanding the conversation', () => {
    render(<BackgroundTaskEventItem conversationId="conv-1" event={mocks.events[0] as never} />)
    fireEvent.click(screen.getByText('Background result'))
    expect(usePanelStore.getState().view).toEqual({
      type: 'background-task-result',
      conversationId: 'conv-1',
      event: mocks.events[0],
    })
    expect(screen.queryByText('pnpm build')).not.toBeInTheDocument()
    expect(screen.queryByText('Compiled successfully')).not.toBeInTheDocument()
    expect(screen.queryByText('artifact://report')).not.toBeInTheDocument()
  })

  it('shows the selected result in preview and closes it without changing the timeline', async () => {
    const event = mocks.events[0]
    const laterEvent = { ...event, id: 'event-2', summary: 'Second execution finished' }
    render(
      <>
        <section aria-label="Conversation">
          <BackgroundTaskEventItem conversationId="conv-1" event={event as never} />
          <BackgroundTaskEventItem conversationId="conv-1" event={laterEvent as never} />
        </section>
        <PreviewHost />
      </>,
    )
    const timeline = within(screen.getByRole('region', { name: 'Conversation' }))
    fireEvent.click(timeline.getAllByRole('button')[0])
    const preview = within(await screen.findByRole('complementary', { name: 'Result preview' }))
    expect(preview.getByText('pnpm build')).toBeVisible()
    expect(preview.getByText('Compiled successfully')).toBeVisible()
    expect(timeline.queryByText('pnpm build')).not.toBeInTheDocument()

    fireEvent.click(timeline.getAllByRole('button')[1])
    expect(preview.getByText('Second execution finished')).toBeVisible()
    expect(preview.queryByText('Build finished')).not.toBeInTheDocument()
    fireEvent.click(preview.getByRole('button', { name: 'close' }))
    expect(usePanelStore.getState().view.type).toBe('closed')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    fireEvent.click(timeline.getAllByRole('button')[0])
    expect(usePanelStore.getState().view).toMatchObject({ event })
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
    expect(screen.getByText('Background result')).toBeInTheDocument()
  })
})
