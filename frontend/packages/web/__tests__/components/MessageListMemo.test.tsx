import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { useMessageStore } from '@cubeplex/core'
import en from '../../messages/en.json'
import { AssistantMessage, HistoryAssistantMessage } from '@/components/chat/AssistantMessage'
import type { AssistantMessage as AssistantMessageType } from '@cubeplex/core'

// MessageActions (rendered inside AssistantMessage) calls useRouter for /fork.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

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
  beforeEach(() => {
    useMessageStore.setState({
      errors: {},
      currentRunId: null,
      streamConnection: null,
      lastRunStatus: null,
      cancellingConversationIds: {},
    })
  })

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

  it('renders an error alert when stop_reason is "error" and content is empty', async () => {
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
    const chip = screen.getByRole('button', { name: 'This run failed' })
    expect(chip).toHaveTextContent('Reply failed')
    chip.click()
    expect(await screen.findByText(/messages\.347/)).toBeInTheDocument()
  })

  it('shows Stopped for a user-aborted reply without hovering', async () => {
    const stopped = {
      id: 'msg-stopped',
      role: 'assistant',
      content: [{ type: 'text', text: 'partial answer' }],
      stop_reason: 'aborted',
      run_id: 'run-stopped',
      timestamp: 1_700_000_002,
    } as unknown as AssistantMessageType
    render(
      <HistoryAssistantMessage
        message={stopped}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
      />,
      { wrapper },
    )
    expect(screen.getByText('partial answer')).toBeInTheDocument()
    const chip = screen.getByRole('button', { name: 'This run was stopped' })
    expect(chip).toHaveTextContent('Stopped')
    chip.click()
    expect(await screen.findByText('You stopped this response.')).toBeInTheDocument()
  })

  it('shows Stopped when the only checkpoint is an empty aborted marker', () => {
    const marker = {
      id: 'msg-marker',
      role: 'assistant',
      content: [],
      stop_reason: 'aborted',
      run_id: 'run-marker',
      metadata: { kind: 'user_stop' },
      timestamp: 1_700_000_003,
    } as unknown as AssistantMessageType
    render(
      <HistoryAssistantMessage
        message={marker}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
      />,
      { wrapper },
    )
    expect(screen.getByRole('button', { name: 'This run was stopped' })).toHaveTextContent(
      'Stopped',
    )
  })

  it('paints last_run_error only on the last assistant of that run', () => {
    useMessageStore.setState({
      errors: {
        'conv-1': {
          runId: 'run-1',
          data: { error_code: 'internal_error', message: 'event too large' },
        },
      },
    })
    const midTurn = {
      id: 'msg-mid',
      role: 'assistant',
      content: [{ type: 'text', text: 'looking around' }],
      stop_reason: 'tool_use',
      run_id: 'run-1',
      timestamp: 1_700_000_000,
    } as unknown as AssistantMessageType
    const lastTurn = {
      ...midTurn,
      id: 'msg-last',
      content: [{ type: 'text', text: 'almost done' }],
    } as unknown as AssistantMessageType

    const { rerender } = render(
      <HistoryAssistantMessage
        message={midTurn}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
        isRunAnchor={false}
      />,
      { wrapper },
    )
    expect(screen.queryByRole('button', { name: 'This run failed' })).not.toBeInTheDocument()

    rerender(
      <HistoryAssistantMessage
        message={lastTurn}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
        isRunAnchor
      />,
    )
    expect(screen.getByRole('button', { name: 'This run failed' })).toHaveTextContent(
      'Reply failed',
    )
  })
})
