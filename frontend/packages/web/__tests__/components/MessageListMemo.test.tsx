import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import { AssistantMessage, HistoryAssistantMessage } from '@/components/chat/AssistantMessage'
import type { AssistantMessage as AssistantMessageType } from '@cubebox/core'

const baseMessage = {
  id: 'msg-1',
  role: 'assistant',
  content: [{ type: 'text', text: 'hello world' }],
  timestamp: 1_700_000_000,
} as unknown as AssistantMessageType

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

describe('HistoryAssistantMessage', () => {
  it('is a memoized re-export of AssistantMessage', () => {
    const marker = HistoryAssistantMessage as unknown as {
      $$typeof?: symbol
      type?: unknown
    }
    expect(marker.$$typeof).toBe(Symbol.for('react.memo'))
    expect(marker.type).toBe(AssistantMessage)
  })

  it('still renders the message text', () => {
    render(
      <HistoryAssistantMessage
        message={baseMessage}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
      />,
      { wrapper },
    )
    expect(screen.getByText('hello world')).toBeInTheDocument()
  })

  it('renders an error alert when stop_reason is "error" and content is empty', () => {
    const errored = {
      id: 'msg-err',
      role: 'assistant',
      content: [],
      stop_reason: 'error',
      error_message: '400 Bad Request: messages.347: empty content',
      timestamp: 1_700_000_001,
    } as unknown as AssistantMessageType
    render(
      <HistoryAssistantMessage
        message={errored}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
      />,
      { wrapper },
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/messages\.347/)
  })
})
