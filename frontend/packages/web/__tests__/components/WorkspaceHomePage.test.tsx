import { Suspense } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import WorkspaceHomePage from '../../app/(app)/w/[wsId]/page'
import { getPresetSelectionStore } from '../../lib/stores/preset-selection'

const storeMocks = vi.hoisted(() => ({
  push: vi.fn(),
  createConversation: vi.fn(),
  setConversationState: vi.fn(),
  renameConversation: vi.fn(),
  send: vi.fn(),
  cancelSteer: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  markSkipHydrate: vi.fn(),
  hydrate: vi.fn(),
  attachedIds: vi.fn(),
  setWorkspaceId: vi.fn(),
  closePanel: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: storeMocks.push,
  }),
}))

vi.mock('@cubeplex/core', () => {
  const attachmentState = {
    upload: storeMocks.upload,
    clear: storeMocks.clear,
    markSkipHydrate: storeMocks.markSkipHydrate,
    hydrate: storeMocks.hydrate,
    attachedIds: storeMocks.attachedIds,
    staging: {},
  }
  const useAttachmentStore = (selector: (state: typeof attachmentState) => unknown): unknown =>
    selector(attachmentState)
  useAttachmentStore.getState = (): typeof attachmentState => attachmentState

  return {
    ApiError: class ApiError extends Error {},
    createApiClient: () => ({
      setWorkspaceId: storeMocks.setWorkspaceId,
    }),
    useAttachmentStore,
    useConversationStore: Object.assign(
      (
        selector?: (state: {
          create: typeof storeMocks.createConversation
          rename: typeof storeMocks.renameConversation
          conversations: unknown[]
        }) => unknown,
      ) => {
        const state = {
          create: storeMocks.createConversation,
          rename: storeMocks.renameConversation,
          conversations: [] as unknown[],
        }
        return selector ? selector(state) : state
      },
      { setState: storeMocks.setConversationState },
    ),
    usePanelStore: Object.assign(
      (
        selector: (state: {
          view: { type: string }
          openSandbox: () => void
          close: () => void
        }) => unknown,
      ) =>
        selector({
          view: { type: 'closed' },
          openSandbox: vi.fn(),
          close: storeMocks.closePanel,
        }),
      { getState: () => ({ close: storeMocks.closePanel }) },
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
        cancellingConversationIds: Record<string, true>
        runLifecycle: Record<string, string>
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
        cancellingConversationIds: {},
        runLifecycle: {},
      }),
  }
})

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

vi.mock('@cubeplex/core/hooks/useDeploymentMode', () => ({
  useDeploymentMode: () => ({ sandboxEnabled: true }),
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({ theme: 'light', resolvedTheme: 'light', setTheme: vi.fn() }),
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
    getPresetSelectionStore('ws-1').setState({
      modelKey: null,
      thinking: 'medium',
      presets: [],
      presetFetchStatus: 'idle',
      presetFetchError: null,
    })
    // AppShell uses useMediaQuery → window.matchMedia (jsdom has none by default).
    // matches:false → mobile branch (no react-resizable-panels Group mount).
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })
  })

  it('closes a leftover panel from the previous conversation on mount', async () => {
    await act(async () => {
      renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    expect(storeMocks.closePanel).toHaveBeenCalled()
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

  it('does not create a conversation after a successful preset fetch confirms there are no usable presets', async () => {
    await act(async () => {
      renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    const input = await screen.findByTestId('chat-input')
    getPresetSelectionStore('ws-1').setState({
      presets: [],
      presetFetchStatus: 'ready',
      presetFetchError: null,
    })
    fireEvent.change(input, { target: { value: 'Hello in workspace 1' } })
    await waitFor(() => expect(screen.getByTestId('send-button')).toBeDisabled())
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(storeMocks.createConversation).not.toHaveBeenCalled()
    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('does not clear the composer when conversation creation fails', async () => {
    storeMocks.createConversation.mockRejectedValue(new Error('network blip'))
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await act(async () => {
      renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    const input = await screen.findByTestId('chat-input')
    fireEvent.change(input, { target: { value: 'Hello in workspace 1' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.createConversation).toHaveBeenCalledTimes(1)
    })
    // The failed send must not be silently absorbed: no navigation, and the
    // user's message stays in the box instead of vanishing.
    expect(storeMocks.push).not.toHaveBeenCalled()
    expect(input).toHaveValue('Hello in workspace 1')

    consoleError.mockRestore()
  })

  it('loads an office task template into the composer', async () => {
    await act(async () => {
      renderWithIntl(<WorkspaceHomePage params={Promise.resolve({ wsId: 'ws-1' })} />)
      await Promise.resolve()
    })

    const input = await screen.findByTestId('chat-input')
    fireEvent.click(screen.getByRole('button', { name: /Create a document or deck/ }))

    expect(input).toHaveValue(en.home.promptCards.document.prompt)
  })
})
