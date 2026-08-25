import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import { useMessageStore } from '@cubeplex/core'
import type { Message } from '@cubeplex/core'
import en from '../../messages/en.json'
import { MessageList } from '@/components/chat/MessageList'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const CONVERSATION_ID = 'conv-loading'
let finishHistoryLoad: () => void
const loadedMessage = {
  id: 'msg-loaded',
  role: 'user',
  content: [{ type: 'text', text: 'Loaded history' }],
  timestamp: 1_700_000_000,
} as unknown as Message

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

describe('MessageList history loading', () => {
  beforeEach(() => {
    const loadFinished = new Promise<void>((resolve) => {
      finishHistoryLoad = resolve
    })
    useMessageStore.setState({
      messages: {},
      loadingMessagesByConv: {},
      loadMessages: vi.fn(async () => {
        useMessageStore.setState({
          loadingMessagesByConv: { [CONVERSATION_ID]: true },
        } as never)
        await loadFinished
        useMessageStore.setState({
          messages: { [CONVERSATION_ID]: [loadedMessage] },
          loadingMessagesByConv: { [CONVERSATION_ID]: false },
        } as never)
      }),
      loadOlderMessages: vi.fn(async () => {}),
      loadOlderUntilSeq: vi.fn(async () => {}),
      isStreaming: false,
      streamingConversationId: null,
      streamAgents: {},
      errors: {},
      pendingAsk: null,
      pendingConfirmMap: {},
      cancellingConversationIds: {},
      failoverEvents: {},
      retryEvents: {},
      lastRunStatus: null,
      todos: [],
      toolResultMap: {},
      hasMoreByConv: {},
      loadingOlderByConv: {},
      oldestSeqByConv: {},
    } as never)
  })

  it('moves from a progress announcement to the loaded history', async () => {
    render(<MessageList conversationId={CONVERSATION_ID} />, { wrapper })

    expect(await screen.findByRole('status', { name: 'Loading conversation…' })).toBeVisible()

    await act(async () => {
      finishHistoryLoad()
    })

    expect(await screen.findByText('Loaded history')).toBeVisible()
    expect(screen.queryByRole('status', { name: 'Loading conversation…' })).not.toBeInTheDocument()
  })
})
