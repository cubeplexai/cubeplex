import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

// MessageActions (rendered inside MessageList) calls useRouter for /fork.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import { MessageList } from '@/components/chat/MessageList'
import { useMessageStore, type Message } from '@cubeplex/core'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

const realUserMessage = {
  id: 'msg-user',
  role: 'user',
  content: [{ type: 'text', text: 'real human question' }],
  timestamp: 1_700_000_000,
} as unknown as Message

// Mirrors a cubepi synthetic_user_message after the API round-trip
// (e.g. a todo-guard nudge persisted into history).
const syntheticMessage = {
  id: 'msg-synthetic',
  role: 'user',
  content: [{ type: 'text', text: 'The todo list still has unfinished items.' }],
  timestamp: 1_700_000_001,
  metadata: { synthetic: true, synthetic_source: 'todo_guard' },
} as unknown as Message

describe('MessageList synthetic message handling', () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: { 'conv-1': [realUserMessage, syntheticMessage] },
      loadMessages: vi.fn(async () => {}),
      isStreaming: false,
      mainStream: null,
      subAgentStreams: {},
    } as never)
  })

  it('renders real user messages as bubbles but hides synthetic ones', () => {
    render(<MessageList conversationId="conv-1" />, { wrapper })

    expect(screen.getByText('real human question')).toBeInTheDocument()
    expect(screen.queryByText('The todo list still has unfinished items.')).not.toBeInTheDocument()
  })
})
