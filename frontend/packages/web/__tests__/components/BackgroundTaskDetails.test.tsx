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
vi.mock('@/hooks/useSandboxFileContent', () => ({ useSandboxFileContent: mocks.output }))
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

  it('matches tool call IDs and reads conversation logs', () => {
    mount(task)
    expect(screen.getByText(/echo original/)).toHaveTextContent('"timeout": 90')
    expect(screen.getByText('Original tool response')).toBeVisible()
    expect(screen.getByText('Final command output')).toBeVisible()
    expect(screen.queryByText(/wrong command/)).not.toBeInTheDocument()
    expect(mocks.output).toHaveBeenCalledWith('ws-1', '/workspace/result.log', 'conv-1', 0)
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
    expect(mocks.output).toHaveBeenLastCalledWith('ws-1', '/workspace/result.log', 'conv-1', 5000)
    expect(mocks.refresh).not.toHaveBeenCalled()
    act(() =>
      view.rerender(
        <SWRConfig value={{ provider: () => new Map() }}>
          <BackgroundTaskDetails conversationId="conv-1" taskId="bgt-1" task={task} />
        </SWRConfig>,
      ),
    )
    expect(mocks.output).toHaveBeenLastCalledWith('ws-1', '/workspace/result.log', 'conv-1', 0)
    expect(mocks.refresh).toHaveBeenCalledOnce()
  })
})
