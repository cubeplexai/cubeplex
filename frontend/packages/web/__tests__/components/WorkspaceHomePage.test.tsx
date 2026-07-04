import { Suspense } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import WorkspaceHomePage from '../../app/(app)/w/[wsId]/page'

const storeMocks = vi.hoisted(() => ({
  push: vi.fn(),
  createConversation: vi.fn(),
  setConversationState: vi.fn(),
  renameConversation: vi.fn(),
  send: vi.fn(),
  cancelSteer: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  hydrate: vi.fn(),
  attachedIds: vi.fn(),
  setWorkspaceId: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: storeMocks.push,
  }),
}))

vi.mock('@cubebox/core', () => {
  const attachmentState = {
    upload: storeMocks.upload,
    clear: storeMocks.clear,
    hydrate: storeMocks.hydrate,
    attachedIds: storeMocks.attachedIds,
    staging: {},
  }
  const useAttachmentStore = (selector: (state: typeof attachmentState) => unknown): unknown =>
    selector(attachmentState)
  useAttachmentStore.getState = (): typeof attachmentState => attachmentState

  return {
    createApiClient: () => ({
      setWorkspaceId: storeMocks.setWorkspaceId,
    }),
    useAttachmentStore,
    useConversationStore: Object.assign(
      (
        selector?: (state: {
          create: typeof storeMocks.createConversation
          rename: typeof storeMocks.renameConversation
        }) => unknown,
      ) => {
        const state = {
          create: storeMocks.createConversation,
          rename: storeMocks.renameConversation,
        }
        return selector ? selector(state) : state
      },
      { setState: storeMocks.setConversationState },
    ),
    useMessageStore: (
      selector: (state: {
        send: typeof storeMocks.send
        cancelSteer: typeof storeMocks.cancelSteer
        pendingSteers: Record<string, unknown[]>
        pendingConfirmMap: Record<string, unknown>
        pendingAsk: unknown | null
        isStreaming: boolean
        streamingConversationId: string | null
      }) => unknown,
    ) =>
      selector({
        send: storeMocks.send,
        cancelSteer: storeMocks.cancelSteer,
        pendingSteers: {},
        pendingConfirmMap: {},
        pendingAsk: null,
        isStreaming: false,
        streamingConversationId: null,
      }),
  }
})

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Suspense fallback={null}>{ui}</Suspense>
    </NextIntlClientProvider>,
  )
}

describe('WorkspaceHomePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeMocks.createConversation.mockResolvedValue({ id: 'conv-1' })
    storeMocks.renameConversation.mockResolvedValue({ id: 'conv-1', title: 'Reply with' })
    storeMocks.send.mockResolvedValue(undefined)
    storeMocks.attachedIds.mockReturnValue(['file-1'])
  })

  it('eagerly creates a draft conversation on file pick and uploads to it', async () => {
    let view!: ReturnType<typeof render>
    await act(async () => {
      view = renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    await screen.findByTestId('chat-input')
    const fileInput = view.container.querySelector('input[type="file"]')
    expect(fileInput).toBeInstanceOf(HTMLInputElement)
    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' })

    await act(async () => {
      fireEvent.change(fileInput!, { target: { files: [file] } })
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(storeMocks.createConversation).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(storeMocks.upload).toHaveBeenCalledWith(expect.anything(), 'conv-1', [file])
    })
    expect(storeMocks.push).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalledWith(
        expect.anything(),
        'conv-1',
        '',
        ['file-1'],
        expect.any(Array),
        // The home page now forwards the composer's model + reasoning choice
        // on the first send (mirrors InputBar.handleSubmit), so the bug where
        // turn-1 silently shipped a different thinking level than turn-2 (which
        // honored the dropdown) can't recur. `medium` is the store default.
        { model_key: null, reasoning: { mode: 'on', effort: 'medium', summary: 'none' } },
      )
    })
    expect(storeMocks.push).toHaveBeenCalledWith('/w/ws-1/conversations/conv-1')
    // Conversation creation is cached — second call (on submit) does NOT re-create.
    expect(storeMocks.createConversation).toHaveBeenCalledTimes(1)
  })
})
