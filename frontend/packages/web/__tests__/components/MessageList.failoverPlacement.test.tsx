import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import { useMessageStore } from '@cubeplex/core'
import type { FailoverEvent, Message } from '@cubeplex/core'
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

const CONV = 'conv-failover-place'

const failoverEvent: FailoverEvent = {
  type: 'model_failover',
  timestamp: '2026-08-16T12:00:00Z',
  agent_id: null,
  agent_name: null,
  data: {
    failed_ref: 'primary/m1',
    next_ref: 'backup/m1',
    reason: 'rate limited',
  },
}

const userMsg = {
  id: 'msg-user',
  role: 'user',
  content: [{ type: 'text', text: 'hello' }],
  timestamp: 1_700_000_000,
} as unknown as Message

const assistantMsg = {
  id: 'msg-asst',
  role: 'assistant',
  content: [{ type: 'text', text: 'backup answer' }],
  timestamp: 1_700_000_001,
  run_id: 'run-1',
} as unknown as Message

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

function expectBannerAboveAnswer() {
  const banner = screen.getByTestId('failover-banner')
  const answer = screen.getByText('backup answer')
  expect(banner.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
}

describe('MessageList failover banner placement', () => {
  beforeEach(() => {
    useMessageStore.setState({
      loadMessages: vi.fn(async () => {}),
      loadOlderMessages: vi.fn(async () => {}),
      loadOlderUntilSeq: vi.fn(async () => {}),
      failoverEvents: { [CONV]: [failoverEvent] },
      retryEvents: {},
      errors: {},
      pendingAsk: null,
      pendingConfirmMap: {},
      cancellingConversationIds: {},
      lastRunStatus: null,
      todos: [],
      toolResultMap: {},
      hasMoreByConv: {},
      loadingOlderByConv: {},
      oldestSeqByConv: {},
    } as never)
  })

  it('sits above the live assistant while the turn is streaming', () => {
    useMessageStore.setState({
      messages: { [CONV]: [userMsg] },
      isStreaming: true,
      streamingConversationId: CONV,
      streamAgents: {
        main: {
          text: 'backup answer',
          toolCalls: [],
          toolResults: [],
          thinking: '',
          blocks: [{ type: 'text', text: 'backup answer' }],
          name: null,
        },
      },
    } as never)

    render(<MessageList conversationId={CONV} />, { wrapper })
    expectBannerAboveAnswer()
  })

  it('stays above the committed assistant after the stream finalizes', () => {
    useMessageStore.setState({
      messages: { [CONV]: [userMsg, assistantMsg] },
      isStreaming: false,
      streamingConversationId: null,
      streamAgents: {},
    } as never)

    render(<MessageList conversationId={CONV} />, { wrapper })
    expectBannerAboveAnswer()
  })
})
