import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'

const cancelActiveRun = vi.hoisted(() => vi.fn().mockResolvedValue({ status: 'cancelled' }))

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return { ...actual, cancelActiveRun }
})
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

import { useMessageStore } from '@cubeplex/core'
import { MessageList } from '@/components/chat/MessageList'

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

describe('MessageList pending HITL cancellation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.setState({
      messages: { 'conv-1': [] },
      loadMessages: vi.fn(async () => {}),
      cancelStream: vi.fn(async () => {}),
      isStreaming: false,
      streamingConversationId: 'conv-1',
      currentRunId: 'run-1',
      pendingAsk: {
        question_id: 'question-1',
        questions: [],
        timeout_seconds: null,
        requestedAt: Date.now(),
        run_id: 'run-1',
      },
      pendingConfirmMap: {},
      cancellingConversationIds: {},
      runLifecycle: { 'conv-1': 'paused_hitl' },
    } as never)
  })

  it('routes the form Cancel action through HITL resume instead of hard Stop', async () => {
    render(<MessageList conversationId="conv-1" />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(cancelActiveRun).toHaveBeenCalledOnce())
    expect(useMessageStore.getState().cancelStream).not.toHaveBeenCalled()
    expect(useMessageStore.getState().runLifecycle['conv-1']).toBe('resuming_hitl')
  })
})
