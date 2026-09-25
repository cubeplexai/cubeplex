import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NextIntlClientProvider } from 'next-intl'
import en from '../../messages/en.json'
import type { ApiClient, AskQuestion, PendingAsk } from '@cubeplex/core'

const submitAskUserAnswer = vi.hoisted(() => vi.fn().mockResolvedValue({ status: 'accepted' }))

vi.mock('@cubeplex/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@cubeplex/core')>()
  return { ...actual, submitAskUserAnswer }
})
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: () => undefined }),
}))

import { useMessageStore } from '@cubeplex/core'
import { AskUserCard } from '@/components/chat/AskUserCard'
import { AskUserResolvedCard } from '@/components/chat/AskUserResolvedCard'
import { MessageList } from '@/components/chat/MessageList'

const realLoadMessages = useMessageStore.getState().loadMessages

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)

const repoQuestion: AskQuestion = {
  key: 'repo',
  prompt: 'Which repository?',
  options: [
    { label: 'Main', value: 'main', description: null, allow_input: false },
    { label: 'Other repository', value: 'repo_url', description: null, allow_input: true },
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
    get: vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    }),
  } as unknown as ApiClient
}

async function chooseCustomRepo(url: string) {
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()

  fireEvent.click(screen.getByRole('radio', { name: 'Main' }))
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()

  fireEvent.click(screen.getByRole('radio', { name: 'Other repository' }))
  const input = screen.getByRole('textbox', { name: 'Custom answer for Other repository' })
  expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
  fireEvent.change(input, { target: { value: '   ' } })
  expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
  fireEvent.change(input, { target: { value: `  ${url}  ` } })
  expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
}

describe('Ask User allow_input', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useMessageStore.getState().clearStream()
    useMessageStore.setState({
      messages: { 'conv-1': [] },
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

  it('accepts custom text for a live ask_user_request', async () => {
    useMessageStore.setState({ currentRunId: 'run-live' })
    useMessageStore.getState().__applyEvent({
      type: 'ask_user_request',
      event_id: 'ev-live',
      timestamp: '2026-09-24T00:00:00.000Z',
      agent_id: null,
      agent_name: null,
      data: {
        question_id: 'q-live',
        questions: [repoQuestion],
        timeout_seconds: null,
      },
    })
    useMessageStore.setState({
      streamingConversationId: 'conv-1',
      messages: { 'conv-1': [] },
      loadMessages: vi.fn(async () => {}),
    })
    expect(useMessageStore.getState().pendingAsk?.questions[0].options?.[1].allow_input).toBe(true)

    render(<MessageList conversationId="conv-1" />, { wrapper })
    await chooseCustomRepo('https://github.com/acme/app')

    await waitFor(() => expect(submitAskUserAnswer).toHaveBeenCalledOnce())
    expect(submitAskUserAnswer.mock.calls[0][2]).toBe('q-live')
    expect(submitAskUserAnswer.mock.calls[0][3]).toEqual({
      repo: 'https://github.com/acme/app',
    })
  })

  it('accepts custom text for a reloaded pending question', async () => {
    const client = makeBootstrapClient({
      messages: [],
      active_run: null,
      total: 0,
      last_run_status: null,
      pending_hitl: {
        run_id: 'run-reload',
        question_id: 'q-reload',
        kind: 'ask_user',
        requested_at: '2026-09-24T00:00:00.000Z',
        questions: [repoQuestion],
      },
    })
    await realLoadMessages(client, 'conv-1')
    useMessageStore.setState({
      loadMessages: vi.fn(async () => {}),
      // Drop the run id so the bootstrap reattach loop does not keep fetching.
      currentRunId: null,
    })
    expect(useMessageStore.getState().pendingAsk?.questions[0].options?.[1].allow_input).toBe(true)
    expect(useMessageStore.getState().streamingConversationId).toBe('conv-1')

    render(<MessageList conversationId="conv-1" />, { wrapper })
    await chooseCustomRepo('https://github.com/acme/web')

    await waitFor(() => expect(submitAskUserAnswer).toHaveBeenCalledOnce())
    expect(submitAskUserAnswer.mock.calls[0][2]).toBe('q-reload')
    expect(submitAskUserAnswer.mock.calls[0][3]).toEqual({
      repo: 'https://github.com/acme/web',
    })
  })

  it('submits a fixed option value and a multi-select custom value', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const pending: PendingAsk = {
      question_id: 'q-multi',
      questions: [
        {
          key: 'tags',
          prompt: 'Tags',
          options: [
            { label: 'A', value: 'a', description: null, allow_input: false },
            { label: 'Other', value: 'other', description: null, allow_input: true },
          ],
          multi_select: true,
          required: true,
        },
      ],
      timeout_seconds: null,
      requestedAt: Date.now(),
      run_id: 'run-1',
    }
    render(<AskUserCard pending={pending} onSubmit={onSubmit} />, { wrapper })

    fireEvent.click(screen.getByRole('checkbox', { name: 'A' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Other' }))
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: 'Custom answer for Other' }), {
      target: { value: 'zzz' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ tags: ['a', 'zzz'] }))
  })

  it('shows typed text on the resolved card when it is not an option value', () => {
    render(
      <AskUserResolvedCard
        questions={[repoQuestion]}
        resultContent={`User answers:\n${JSON.stringify({ repo: 'https://github.com/acme/app' })}`}
      />,
    )
    expect(screen.getByText('https://github.com/acme/app')).toBeTruthy()
  })
})
