import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { UserEvent } from '@cubebox/core'
import { MemoryUpdateChip } from '../../../components/chat/MemoryUpdateChip'

const CONVERSATION_ID = 'conv-abc'

const mockEvent = (op: 'save' | 'update' = 'save'): UserEvent => ({
  id: 'uev-1',
  type: 'memory_updated',
  workspace_id: 'ws-1',
  payload: {
    conversation_id: CONVERSATION_ID,
    run_id: 'run-1',
    items: [{ op, memory_id: 'mem-1' }],
  },
  created_at: '2026-01-01T00:00:00Z',
})

let storeState: { byConversation: Record<string, UserEvent[]>; markRead: () => void }

vi.mock('@cubebox/core', () => ({
  useMemoryEventStore: (selector: (state: typeof storeState) => unknown) => selector(storeState),
  createApiClient: () => ({
    post: vi.fn().mockResolvedValue({ ok: true }),
  }),
}))

describe('MemoryUpdateChip', () => {
  it('renders nothing when there are no events for the conversation', () => {
    storeState = { byConversation: {}, markRead: vi.fn() }
    const { container } = render(<MemoryUpdateChip conversationId={CONVERSATION_ID} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows "已记住 N 条记忆" for save ops', () => {
    storeState = {
      byConversation: { [CONVERSATION_ID]: [mockEvent('save')] },
      markRead: vi.fn(),
    }
    render(<MemoryUpdateChip conversationId={CONVERSATION_ID} />)
    expect(screen.getByRole('button')).toHaveTextContent('已记住 1 条记忆')
  })

  it('shows "已更新 N 条记忆" when all ops are updates', () => {
    storeState = {
      byConversation: { [CONVERSATION_ID]: [mockEvent('update')] },
      markRead: vi.fn(),
    }
    render(<MemoryUpdateChip conversationId={CONVERSATION_ID} />)
    expect(screen.getByRole('button')).toHaveTextContent('已更新 1 条记忆')
  })

  it('counts total items across multiple events', () => {
    const ev1 = mockEvent('save')
    const ev2: UserEvent = {
      ...mockEvent('save'),
      id: 'uev-2',
      payload: {
        ...mockEvent('save').payload,
        items: [
          { op: 'save', memory_id: 'mem-2' },
          { op: 'save', memory_id: 'mem-3' },
        ],
      },
    }
    storeState = {
      byConversation: { [CONVERSATION_ID]: [ev1, ev2] },
      markRead: vi.fn(),
    }
    render(<MemoryUpdateChip conversationId={CONVERSATION_ID} />)
    expect(screen.getByRole('button')).toHaveTextContent('已记住 3 条记忆')
  })
})
