import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PendingSteers } from '../../components/layout/PendingSteers'

const mocks = vi.hoisted(() => ({
  cancelSteer: vi.fn(),
  setDraft: vi.fn(),
  setWorkspaceId: vi.fn(),
  pending: [] as {
    steerId: string
    text: string
    state: 'submitting' | 'queued' | 'dispatched' | 'failed'
    createdAt: string
  }[],
  lifecycle: 'idle' as 'idle' | 'running',
  isStreaming: false,
  streamingConversationId: null as string | null,
}))

vi.mock('@/hooks/useComposerDraft', () => ({
  useComposerDraft: {
    getState: () => ({ setDraft: mocks.setDraft }),
  },
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
  useMessageStore: (
    sel: (s: {
      pendingSteers: Record<string, unknown>
      cancelSteer: typeof mocks.cancelSteer
      runLifecycle: Record<string, 'idle' | 'running'>
      isStreaming: boolean
      streamingConversationId: string | null
    }) => unknown,
  ) =>
    sel({
      pendingSteers: { 'conv-1': mocks.pending },
      cancelSteer: mocks.cancelSteer,
      runLifecycle: { 'conv-1': mocks.lifecycle },
      isStreaming: mocks.isStreaming,
      streamingConversationId: mocks.streamingConversationId,
    }),
}))
vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) =>
    ({
      pendingSteerFailed: '未发送',
      pendingSteerQueued: '已排队',
      pendingSteerSending: '正在引导…',
      pendingSteerCancel: '取消待处理的引导',
      pendingSteerDismiss: '忽略失败的引导',
      pendingSteerRestore: '将失败的引导恢复到输入框',
    })[key] ?? key,
}))

describe('PendingSteers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.cancelSteer.mockResolvedValue(true)
    mocks.pending = [
      {
        steerId: 's1',
        text: 'do X instead',
        state: 'queued',
        createdAt: '2026-08-12T00:00:00.000Z',
      },
    ]
    mocks.lifecycle = 'idle'
    mocks.isStreaming = false
    mocks.streamingConversationId = null
  })

  it('renders pending steer text and cancels on click', () => {
    render(<PendingSteers conversationId="conv-1" />)
    expect(screen.getByText('do X instead')).toBeInTheDocument()
    expect(screen.getByText('已排队')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消待处理的引导' }))
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })

  it('renders nothing when there are no pending steers', () => {
    mocks.pending = []
    const { container } = render(<PendingSteers conversationId="conv-1" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('does not offer cancellation before the enqueue request finishes', () => {
    mocks.pending[0].state = 'submitting'
    render(<PendingSteers conversationId="conv-1" />)

    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
  })

  it('recovers the full failed text after dismissing its durable row', async () => {
    mocks.pending[0].state = 'failed'
    render(<PendingSteers conversationId="conv-1" />)

    expect(screen.getByText('未发送')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '将失败的引导恢复到输入框' }))
    await waitFor(() =>
      expect(mocks.setDraft).toHaveBeenCalledWith('do X instead', 'conv-1', 'prepend'),
    )
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })

  it('keeps failed text out of the draft when dismissal fails', async () => {
    mocks.pending[0].state = 'failed'
    mocks.cancelSteer.mockResolvedValue(false)
    render(<PendingSteers conversationId="conv-1" />)

    fireEvent.click(screen.getByRole('button', { name: '将失败的引导恢复到输入框' }))

    await waitFor(() => expect(mocks.cancelSteer).toHaveBeenCalledOnce())
    expect(mocks.setDraft).not.toHaveBeenCalled()
  })

  it('offers dismissal instead of recovery while a newer run is active', () => {
    mocks.pending[0].state = 'failed'
    mocks.lifecycle = 'running'
    mocks.isStreaming = true
    mocks.streamingConversationId = 'conv-1'
    render(<PendingSteers conversationId="conv-1" />)

    expect(
      screen.queryByRole('button', { name: '将失败的引导恢复到输入框' }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '忽略失败的引导' }))
    expect(mocks.setDraft).not.toHaveBeenCalled()
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })
})
