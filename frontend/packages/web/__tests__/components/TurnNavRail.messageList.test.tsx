import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

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

function textMessage(id: string, role: 'user' | 'assistant', text: string, seq: number): Message {
  return {
    id,
    role,
    seq,
    content: [{ type: 'text', text }],
    timestamp: seq,
  } as Message
}

describe('MessageList turn navigation', () => {
  beforeEach(() => {
    HTMLElement.prototype.scrollTo = vi.fn()
    useMessageStore.setState({
      messages: {
        'conv-1': [
          textMessage('u1', 'user', 'first question', 1),
          textMessage('a1', 'assistant', 'first answer', 2),
          {
            id: 'syn',
            role: 'user',
            seq: 3,
            content: [{ type: 'text', text: 'synthetic nudge' }],
            timestamp: 3,
            metadata: { synthetic: true },
          } as Message,
          textMessage('u2', 'user', 'second question', 4),
          textMessage('a2', 'assistant', 'second answer', 5),
        ],
      },
      loadMessages: vi.fn(async () => {}),
      loadOlderMessages: vi.fn(async () => {}),
      hasMoreByConv: {},
      loadingOlderByConv: {},
      isStreaming: false,
      mainStream: null,
      subAgentStreams: {},
    } as never)
  })

  it('scrolls the matching turn smoothly and flashes it, skipping synthetic rows', () => {
    render(<MessageList conversationId="conv-1" />, { wrapper })

    const buttons = screen.getAllByRole('button', { name: /Jump to turn/ })
    expect(buttons).toHaveLength(2)
    expect(screen.queryByRole('button', { name: /synthetic nudge/ })).not.toBeInTheDocument()

    fireEvent.click(buttons[0])

    const scroller = document.querySelector('[data-slot="scroll-area"]')
    expect(scroller?.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }))
    const highlighted = document.querySelectorAll('[data-turn-id="u1"]')
    expect(highlighted).toHaveLength(2)
    for (const node of highlighted) {
      expect(node.classList.contains('turn-nav-highlight')).toBe(true)
    }
    expect(document.querySelector('[data-turn-id="syn"]')).toBeNull()
    expect(document.querySelector('[data-turn-id="u2"]')).not.toBeNull()
  })

  it('asks the message store to load older history from the rail mark', () => {
    const loadOlderMessages = vi.fn(async () => {})
    useMessageStore.setState({
      hasMoreByConv: { 'conv-1': true },
      loadOlderMessages,
    } as never)

    render(<MessageList conversationId="conv-1" />, { wrapper })

    expect(screen.queryByRole('button', { name: 'Load earlier turns' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Load earlier turns' }))
    expect(loadOlderMessages).toHaveBeenCalledWith(expect.anything(), 'conv-1')
  })
})
