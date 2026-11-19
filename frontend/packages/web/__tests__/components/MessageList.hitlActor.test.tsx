import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import type { ApiClient, AskQuestion } from '@cubeplex/core'

const submitAskUserAnswer = vi.hoisted(() => vi.fn())
const cancelActiveRun = vi.hoisted(() => vi.fn().mockResolvedValue({ status: 'cancelled' }))
const toastError = vi.hoisted(() => vi.fn())

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return { ...actual, submitAskUserAnswer, cancelActiveRun }
})
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))
vi.mock('sonner', () => ({
  toast: { error: toastError },
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

  it('disables AskUserCard submit for non-actor/pre-hydration and keeps Cancel', async () => {
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

    // Pre-hydration (user is null): must fail closed on Submit while keeping Cancel enabled.
    const view = render(<MessageList conversationId="conv-1" />, { wrapper })
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
    view.unmount()

    // Non-actor participant: sees named guidance, cannot check radio or submit, can cancel.
    useAuthStore.setState({
      user: {
        id: 'usr-viewer',
        email: 'viewer@example.com',
        is_active: true,
        is_superuser: false,
        is_verified: true,
      } as never,
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

  it('disables SandboxConfirmCard approve/deny for non-actor and keeps Cancel', async () => {
    useAuthStore.setState({
      user: {
        id: 'usr-viewer',
        email: 'viewer@example.com',
        is_active: true,
        is_superuser: false,
        is_verified: true,
      } as never,
    })
    const onCancel = vi.fn(async () => {})

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
        onCancel={onCancel}
      />,
      { wrapper },
    )

    expect(
      screen.getByText(
        'Only Daibolin can approve or deny this command. You can cancel to continue.',
      ),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Deny' })).toBeDisabled()
    const cancelBtn = screen.getByRole('button', { name: 'Cancel' })
    expect(cancelBtn).toBeEnabled()
    fireEvent.click(cancelBtn)
    await waitFor(() => expect(onCancel).toHaveBeenCalledOnce())
  })

  it.each([
    [
      'not_execution_actor',
      'Only the user who started this turn can submit an answer. You can cancel to continue.',
    ],
    ['conversation_moved', 'Could not submit response. Conversation state has been refreshed.'],
  ] as const)(
    'resets submitting state, shows toast, and avoids run failure state on 409 (%s)',
    async (errorCode, expectedToast) => {
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
        new ApiError('Conflict', 409, errorCode, {
          code: errorCode,
          message: 'Conflict',
        }),
      )

      render(<MessageList conversationId="conv-1" />, { wrapper })

      fireEvent.click(screen.getByRole('radio', { name: 'Hold' }))
      fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

      await waitFor(() => expect(submitAskUserAnswer).toHaveBeenCalledOnce())
      // Must reset back to "Submit" instead of hanging on "Sending…" forever.
      await waitFor(() => expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled())
      // Toast surfaces the conflict without painting the assistant run as Failed.
      expect(toastError).toHaveBeenCalledWith(expectedToast)
      expect(useMessageStore.getState().errors['conv-1']).toBeNull()
    },
  )

  it('reloads silently without cancelFailed toast when Cancel hits a 409 race', async () => {
    const initialClient = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      last_run_error: null,
      pending_hitl: {
        run_id: 'run-1',
        question_id: 'q-race',
        kind: 'ask_user',
        requested_at: '2026-11-19T00:00:00.000Z',
        questions: [diskQuestion],
      },
    })
    const refreshedClient = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      last_run_error: null,
      pending_hitl: null,
    })
    await realLoadMessages(initialClient, 'conv-1')
    const reloadSpy = vi.fn(async () => {})
    useMessageStore.setState({
      loadMessages: reloadSpy,
      currentRunId: 'run-1',
    })

    cancelActiveRun.mockRejectedValueOnce(
      new ApiError('Conversation moved', 409, 'conversation_moved', {
        code: 'conversation_moved',
        message: 'Conversation moved',
      }),
    )

    render(<MessageList conversationId="conv-1" />, { wrapper })
    reloadSpy.mockClear()
    reloadSpy.mockImplementation((_c, convId) => realLoadMessages(refreshedClient, convId))

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(cancelActiveRun).toHaveBeenCalledOnce())
    await waitFor(() => expect(reloadSpy).toHaveBeenCalledWith(expect.anything(), 'conv-1'))
    await waitFor(() => expect(useMessageStore.getState().pendingAsk).toBeNull())
    expect(toastError).not.toHaveBeenCalled()
    expect(useMessageStore.getState().errors['conv-1']).toBeFalsy()
  })
})
