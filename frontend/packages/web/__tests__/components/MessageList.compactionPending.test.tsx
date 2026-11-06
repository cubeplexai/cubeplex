import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { MessageList } from '@/components/chat/MessageList'
import { useCompactionPendingStore } from '@/lib/stores/compaction-pending'
import { useMessageStore, type Message } from '@cubeplex/core'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const CONV = 'conv-compact-pending'

const userMsg = {
  id: 'msg-user',
  role: 'user',
  content: [{ type: 'text', text: 'real human question' }],
  timestamp: 1_700_000_000,
} as unknown as Message

const savedMarker = {
  id: 'msg-marker',
  role: 'user',
  content: [{ type: 'text', text: '' }],
  timestamp: 1_700_000_001,
  metadata: {
    synthetic: true,
    synthetic_source: 'compaction',
    kind: 'compaction',
    compaction: { source: 'manual' },
  },
} as unknown as Message

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

describe('MessageList compaction waiting line', () => {
  beforeEach(() => {
    useCompactionPendingStore.setState({ compactingConversationIds: {} })
    useMessageStore.setState({
      messages: { [CONV]: [userMsg] },
      loadMessages: vi.fn(async () => {}),
      loadOlderMessages: vi.fn(async () => {}),
      loadOlderUntilSeq: vi.fn(async () => {}),
      isStreaming: false,
      streamingConversationId: null,
      streamAgents: {},
      failoverEvents: {},
      retryEvents: {},
      errors: {},
      pendingAsk: null,
      pendingConfirmMap: {},
    } as never)
  })

  it('draws the waiting divider at the end of this conversation', () => {
    useCompactionPendingStore.getState().beginCompacting(CONV)
    render(<MessageList conversationId={CONV} />, { wrapper })
    expect(screen.getByTestId('compaction-marker')).toHaveTextContent('Compacting context…')
  })

  it('does not draw a second divider once the saved marker is the tail', () => {
    useMessageStore.setState({ messages: { [CONV]: [userMsg, savedMarker] } } as never)
    useCompactionPendingStore.getState().beginCompacting(CONV)
    render(<MessageList conversationId={CONV} />, { wrapper })
    const markers = screen.getAllByTestId('compaction-marker')
    expect(markers).toHaveLength(1)
    expect(markers[0]).toHaveTextContent('Context compacted')
    expect(markers[0]).not.toHaveTextContent('Compacting context…')
  })

  it('ignores a compact that belongs to another conversation', () => {
    useCompactionPendingStore.getState().beginCompacting('conv-other')
    render(<MessageList conversationId={CONV} />, { wrapper })
    expect(screen.queryByTestId('compaction-marker')).not.toBeInTheDocument()
  })
})
