import { fireEvent, render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PendingSteers } from '../../components/layout/PendingSteers'

const mocks = vi.hoisted(() => ({
  cancelSteer: vi.fn(),
  onRecover: vi.fn(),
  setWorkspaceId: vi.fn(),
  pending: [] as {
    steerId: string
    text: string
    state: 'submitting' | 'queued' | 'dispatched' | 'failed'
    createdAt: string
  }[],
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({ setWorkspaceId: mocks.setWorkspaceId }),
  useMessageStore: (
    sel: (s: {
      pendingSteers: Record<string, unknown>
      cancelSteer: typeof mocks.cancelSteer
    }) => unknown,
  ) => sel({ pendingSteers: { 'conv-1': mocks.pending }, cancelSteer: mocks.cancelSteer }),
}))
vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

describe('PendingSteers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.pending = [
      {
        steerId: 's1',
        text: 'do X instead',
        state: 'queued',
        createdAt: '2026-08-12T00:00:00.000Z',
      },
    ]
  })

  it('renders pending steer text and cancels on click', () => {
    render(<PendingSteers conversationId="conv-1" onRecover={mocks.onRecover} />)
    expect(screen.getByText('do X instead')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })

  it('renders nothing when there are no pending steers', () => {
    mocks.pending = []
    const { container } = render(
      <PendingSteers conversationId="conv-1" onRecover={mocks.onRecover} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('does not offer cancellation before the enqueue request finishes', () => {
    mocks.pending[0].state = 'submitting'
    render(<PendingSteers conversationId="conv-1" onRecover={mocks.onRecover} />)

    expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
  })

  it('recovers the full failed text before dismissing its durable row', () => {
    mocks.pending[0].state = 'failed'
    render(<PendingSteers conversationId="conv-1" onRecover={mocks.onRecover} />)

    fireEvent.click(screen.getByRole('button', { name: /restore/i }))
    expect(mocks.onRecover).toHaveBeenCalledWith('do X instead')
    expect(mocks.cancelSteer).toHaveBeenCalledWith(expect.anything(), 'conv-1', 's1')
  })
})
