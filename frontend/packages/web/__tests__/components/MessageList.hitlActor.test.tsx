import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import type { ApiClient, AskQuestion } from '@cubeplex/core'

const submitAskUserAnswer = vi.hoisted(() => vi.fn())
const cancelActiveRun = vi.hoisted(() => vi.fn().mockResolvedValue({ status: 'cancelled' }))

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return { ...actual, submitAskUserAnswer, cancelActiveRun }
})
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

import { ApiError, useAuthStore, useMessageStore } from '@cubeplex/core'
import { MessageList } from '@/components/chat/MessageList'
import { SandboxConfirmCard } from '@/components/chat/SandboxConfirmCard'

const realLoadMessages = useMessageStore.getState().loadMessages

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const diskQuestion: AskQuestion = {
  key: 'action',
  prompt: 'Delete these two disks?',
  options: [
    {
      label: 'Delete disks, keep snapshots',
      value: 'keep_snap',
      description: null,
      allow_input: false,
    },
    { label: 'Hold', value: 'hold', description: null, allow_input: false },
  ],
  multi_select: false,
  required: true,
}

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  )
}

function makeBootstrapClient(body: Record<string, unknown>): ApiClient {
  return {
    get: vi.fn().mockImplementation(async () => Response.json(body)),
  } as unknown as ApiClient
}

describe('HITL execution actor gating and 409 error recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.getState().reset()
    useMessageStore.getState().clearStream()
    useMessageStore.setState({
      messages: { 'conv-1': [] },
      errors: {},
      loadMessages: realLoadMessages,
      cancelStream: vi.fn(async () => {}),
      pendingAsk: null,
      pendingConfirmMap: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
      lastAppliedEventId: null,
    })
  })

  it('disables AskUserCard submit for non-actor participants while keeping Cancel enabled', async () => {
    useAuthStore.setState({
      user: {
        id: 'usr-viewer',
        email: 'viewer@example.com',
        is_active: true,
        is_superuser: false,
        is_verified: true,
      } as never,
    })

    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: {
        run_id: 'run-1',
        question_id: 'q-actor',
        kind: 'ask_user',
        requested_at: '2026-11-19T00:00:00.000Z',
        questions: [diskQuestion],
        actor_user_id: 'usr-daibolin',
        actor_display_name: 'Daibolin',
      },
    })
    await realLoadMessages(client, 'conv-1')
    useMessageStore.setState({
      loadMessages: vi.fn(async () => {}),
      currentRunId: null,
    })

    render(<MessageList conversationId="conv-1" />, { wrapper })

    expect(
      screen.getByText('Only Daibolin can submit an answer. You can cancel to continue.'),
    ).toBeTruthy()
    const radio = screen.getByRole('radio', { name: 'Delete disks, keep snapshots' })
    fireEvent.click(radio)
    expect(radio).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' })
    expect(cancelBtn).toBeEnabled()
    fireEvent.click(cancelBtn)
    await waitFor(() => expect(cancelActiveRun).toHaveBeenCalledOnce())
  })

  it('disables SandboxConfirmCard approve/deny for non-actor participants', () => {
    useAuthStore.setState({
      user: {
        id: 'usr-viewer',
        email: 'viewer@example.com',
        is_active: true,
        is_superuser: false,
        is_verified: true,
      } as never,
    })

    render(
      <SandboxConfirmCard
        pending={{
          question_id: 'q-sbx',
          command: 'aliyun ecs DeleteDisk',
          matched_pattern: 'aliyun *',
          timeout_seconds: null,
          requestedAt: Date.now(),
          run_id: 'run-1',
          actor_user_id: 'usr-daibolin',
          actor_display_name: 'Daibolin',
        }}
        onApprove={vi.fn(async () => {})}
        onDeny={vi.fn(async () => {})}
      />,
      { wrapper },
    )

    expect(screen.getByText('Only Daibolin can approve or deny this command.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled()
  })

  it.each(['not_execution_actor', 'conversation_moved'] as const)(
    'resets submitting state and preserves error banner when submit fails with 409 (%s) on existing history',
    async (errorCode) => {
      const bootstrapPayload = {
        messages: [
          {
            id: 'msg-asst-1',
            seq: 1,
            role: 'assistant',
            content: [{ type: 'text', text: 'Ready to delete disks.' }],
            timestamp: 1700000000,
          },
        ],
        oldest_seq: 1,
        has_more: false,
        active_run: null,
        last_run_status: null,
        last_run_error: null,
        pending_hitl: {
          run_id: 'run-1',
          question_id: 'q-409',
          kind: 'ask_user',
          requested_at: '2026-11-19T00:00:00.000Z',
          questions: [diskQuestion],
        },
      }
      const client = makeBootstrapClient(bootstrapPayload)
      await realLoadMessages(client, 'conv-1')
      useMessageStore.setState({
        currentRunId: null,
        loadMessages: vi.fn((_c, convId) => realLoadMessages(client, convId)),
      })

      submitAskUserAnswer.mockRejectedValueOnce(
        new ApiError('Only the user who started this turn can respond.', 409, errorCode, {
          code: errorCode,
          message: 'Only the user who started this turn can respond.',
        }),
      )

      render(<MessageList conversationId="conv-1" />, { wrapper })

      fireEvent.click(screen.getByRole('radio', { name: 'Hold' }))
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      await waitFor(() => expect(submitAskUserAnswer).toHaveBeenCalledOnce())
      // Must reset back to "Submit" instead of hanging on "Sending…" forever
      await waitFor(() => expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled())
      // Error must NOT be wiped out by the subsequent loadMessages reload
      expect(useMessageStore.getState().errors['conv-1']?.data.message).toBe(
        'Only the user who started this turn can respond.',
      )
    },
  )
})
