import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'
import { useMessageStore, type BackgroundTask, type Message } from '@cubeplex/core'
import { BackgroundTaskDetails } from '@/components/chat/BackgroundTaskDetails'

const mocks = vi.hoisted(() => ({
  getTask: vi.fn(),
  output: vi.fn(),
  refresh: vi.fn(),
}))
vi.mock('@cubeplex/core', async (original) => ({
  ...(await original<typeof import('@cubeplex/core')>()),
  getBackgroundTask: mocks.getTask,
}))
vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))
vi.mock('@/hooks/useBackgroundTaskOutput', () => ({
  useBackgroundTaskOutput: mocks.output,
  backgroundTaskOutputUrl: () => '/task-output',
}))
vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))

const task = {
  id: 'bgt-1',
  tool_call_id: 'tc-1',
  state: 'succeeded',
  revision: 2,
  result_readiness: 'ready',
  result_ref: '/workspace/result.log',
  details: {
    command_kind: 'execute',
    command: 'echo saved',
    log_path: '/workspace/result.log',
    exit_code: 0,
  },
} as BackgroundTask
const messages: Message[] = [
  {
    id: 'assistant-1',
    role: 'assistant',
    content: [
      {
        type: 'tool_call',
        id: 'tc-other',
        name: 'execute',
        arguments: { command: 'wrong command' },
      },
      {
        type: 'tool_call',
        id: 'tc-1',
        name: 'execute',
        arguments: { command: 'echo original', timeout: 90 },
      },
    ],
  },
  {
    id: 'tool-1',
    role: 'tool_result',
    tool_call_id: 'tc-1',
    tool_name: 'execute',
    content: [{ type: 'text', text: 'Original tool response' }],
  },
]

function mount(providedTask?: BackgroundTask) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <BackgroundTaskDetails conversationId="conv-1" taskId="bgt-1" task={providedTask} />
    </SWRConfig>,
  )
}

describe('Background task execution details', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: { 'conv-1': messages },
      backgroundTasks: {},
      streamingConversationId: null,
      streamAgents: {},
      toolResultMap: {},
    })
    mocks.output.mockReturnValue({
      content: 'Final command output',
      loading: false,
      error: null,
      refresh: mocks.refresh,
    })
    mocks.getTask.mockResolvedValue(task)
  })

  it('does not refresh again when an already-completed task is opened', () => {
    mount(task)
    expect(mocks.refresh).not.toHaveBeenCalled()
  })

  it('refreshes when final output becomes ready, without polling delivery revisions', () => {
    const view = mount({ ...task, result_readiness: 'pending' })
    expect(mocks.refresh).not.toHaveBeenCalled()
    const show = (next: BackgroundTask) =>
      view.rerender(
        <SWRConfig value={{ provider: () => new Map() }}>
          <BackgroundTaskDetails conversationId="conv-1" taskId="bgt-1" task={next} />
        </SWRConfig>,
      )
    show(task)
    expect(mocks.refresh).toHaveBeenCalledOnce()
    show({ ...task, revision: task.revision + 1 })
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })

  it('matches tool call IDs and reads conversation logs', () => {
    mount(task)
    expect(screen.getByText(/echo original/)).toHaveTextContent('"timeout": 90')
    expect(screen.getByText('Original tool response')).toBeVisible()
    expect(screen.getByText('Final command output')).toBeVisible()
    expect(screen.queryByText(/wrong command/)).not.toBeInTheDocument()
    expect(mocks.output).toHaveBeenCalledWith('ws-1', 'conv-1', 'bgt-1', 0)
  })

  it('falls back to the saved command without using another conversation stream', () => {
    useMessageStore.setState({
      messages: {},
      streamingConversationId: 'conv-2',
      toolResultMap: { 'tc-1': { content: 'Other conversation response', receivedAt: 1 } },
    })
    mount(task)
    expect(screen.getByText(/echo saved/)).toBeVisible()
    expect(screen.queryByText('Other conversation response')).not.toBeInTheDocument()
  })

  it('loads missing task details independently of message history', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Final command output')).toBeVisible())
    expect(mocks.getTask).toHaveBeenCalledWith(expect.anything(), 'conv-1', 'bgt-1')
  })

  it('preserves the request on log errors and allows retry', () => {
    mocks.output.mockReturnValue({
      content: null,
      loading: false,
      error: new Error('unavailable'),
      refresh: mocks.refresh,
    })
    mount(task)
    expect(screen.getByText(/echo original/)).toBeVisible()
    expect(screen.getByText('outputFailed')).toBeVisible()
    mocks.refresh.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'retry' }))
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })

  it('polls running logs and fetches final output when the task settles', () => {
    const view = mount({ ...task, state: 'running' })
    expect(mocks.output).toHaveBeenLastCalledWith('ws-1', 'conv-1', 'bgt-1', 5000)
    expect(mocks.refresh).not.toHaveBeenCalled()
    act(() =>
      view.rerender(
        <SWRConfig value={{ provider: () => new Map() }}>
          <BackgroundTaskDetails conversationId="conv-1" taskId="bgt-1" task={task} />
        </SWRConfig>,
      ),
    )
    expect(mocks.output).toHaveBeenLastCalledWith('ws-1', 'conv-1', 'bgt-1', 0)
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })
})

describe('persisted subagents and output availability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({ messages: {}, backgroundTasks: {}, streamingConversationId: null })
    mocks.output.mockReturnValue({
      content: null,
      loading: false,
      error: null,
      refresh: mocks.refresh,
    })
  })

  it.each(['metadata', 'details'] as const)('reads inner tool calls from %s', (location) => {
    const summary = {
      text: '',
      thinking: '',
      tool_calls: [{ id: 'tc-1', name: 'execute', arguments: { command: 'inner command' } }],
      tool_results: [{ tool_call_id: 'tc-1', tool_name: 'execute', content: 'inner result' }],
    }
    const events = [
      { type: 'tool_call', id: 'tc-1', name: 'execute', arguments: { command: 'inner command' } },
      { type: 'tool_result', tool_call_id: 'tc-1', name: 'execute', result: 'inner result' },
    ]
    useMessageStore.setState({
      messages: {
        'conv-1': [
          {
            id: 'subagent-result',
            role: 'tool_result',
            tool_call_id: 'outer-call',
            tool_name: 'agent',
            content: [],
            [location]: { subagent_events: location === 'metadata' ? summary : events },
          },
        ],
      },
    })
    mount(task)
    expect(screen.getByText(/inner command/)).toBeVisible()
    expect(screen.getByText('inner result')).toBeVisible()
  })

  it('offers a download for logs above the preview limit', () => {
    mocks.output.mockReturnValue({
      content: null,
      loading: false,
      error: new Error('FILE_TOO_LARGE'),
      refresh: mocks.refresh,
    })
    mount(task)
    expect(screen.getByRole('link', { name: 'downloadOutput' })).toHaveAttribute(
      'href',
      '/task-output?download=true',
    )
    expect(screen.queryByRole('button', { name: 'retry' })).not.toBeInTheDocument()
  })

  it('shows authoritative unavailability even when the task snapshot is still ready', () => {
    const error = new Error('Final output could not be recovered')
    error.name = 'OUTPUT_UNAVAILABLE'
    mocks.output.mockReturnValue({
      content: 'stale partial log',
      loading: false,
      error,
      refresh: mocks.refresh,
    })
    mount(task)
    expect(screen.getByText('Final output could not be recovered')).toBeVisible()
    expect(screen.queryByText('stale partial log')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'retry' })).not.toBeInTheDocument()
  })

  it('does not read or refresh output declared unavailable by the backend', () => {
    mocks.output.mockReturnValue({
      content: 'partial log',
      loading: false,
      error: null,
      refresh: mocks.refresh,
    })
    mount({ ...task, result_readiness: 'unavailable', result_unavailable_reason: 'instance_gone' })
    expect(mocks.output).toHaveBeenCalledWith('ws-1', 'conv-1', null, 0)
    expect(mocks.refresh).not.toHaveBeenCalled()
    expect(screen.getByText('instance_gone')).toBeVisible()
    expect(screen.queryByText('partial log')).not.toBeInTheDocument()
  })
})
