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
  send: vi.fn(),
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
      (selector?: (state: { create: typeof storeMocks.createConversation }) => unknown) => {
        const state = { create: storeMocks.createConversation }
        return selector ? selector(state) : state
      },
      { setState: storeMocks.setConversationState },
    ),
    useMessageStore: (
      selector: (state: {
        send: typeof storeMocks.send
        isStreaming: boolean
        streamingConversationId: string | null
      }) => unknown,
    ) =>
      selector({
        send: storeMocks.send,
        isStreaming: false,
        streamingConversationId: null,
      }),
  }
})

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

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
    storeMocks.send.mockResolvedValue(undefined)
    storeMocks.attachedIds.mockReturnValue(['file-1'])
  })

  it('uploads staged files before navigating to the created conversation', async () => {
    const upload = deferred<void>()
    storeMocks.upload.mockReturnValue(upload.promise)
    let view!: ReturnType<typeof render>
    await act(async () => {
      view = renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    await screen.findByTestId('chat-input')
    const fileInput = view.container.querySelector('input[type="file"]')
    expect(fileInput).toBeInstanceOf(HTMLInputElement)
    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' })
    fireEvent.change(fileInput!, { target: { files: [file] } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.upload).toHaveBeenCalledWith(expect.anything(), 'conv-1', [file])
    })
    expect(storeMocks.push).not.toHaveBeenCalled()

    upload.resolve()

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalledWith(expect.anything(), 'conv-1', '', ['file-1'])
    })
    expect(storeMocks.push).toHaveBeenCalledWith('/w/ws-1/conversations/conv-1')
  })
})
