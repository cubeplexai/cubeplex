import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import { InputBar } from '../../components/layout/InputBar'
import { getPresetSelectionStore } from '../../lib/stores/preset-selection'
import { ApiError } from '@cubeplex/core'
import { toast } from 'sonner'

const storeMocks = vi.hoisted(() => ({
  send: vi.fn(),
  loadMessages: vi.fn(),
  steer: vi.fn(),
  cancelStream: vi.fn(),
  cancelSteer: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  hydrate: vi.fn(),
  setWorkspaceId: vi.fn(),
  compactConversation: vi.fn().mockResolvedValue({ ok: true, compacted: false }),
  appendHistoryMessage: vi.fn(),
  state: {
    isStreaming: false,
    streamingConversationId: null as string | null,
    cancellingConversationIds: {} as Record<string, true>,
    runLifecycle: {} as Record<string, string>,
    pendingAsk: null as unknown | null,
    pendingSteers: {} as Record<string, unknown[]>,
    attachedIds: [] as string[],
  },
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), message: vi.fn(), error: vi.fn() },
}))

vi.mock('@cubeplex/core', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
      public code: string | null,
      public detail: unknown,
    ) {
      super(message)
    }
  },
  createApiClient: () => ({
    setWorkspaceId: storeMocks.setWorkspaceId,
  }),
  compactConversation: storeMocks.compactConversation,
  useMessageStore: (
    selector: (state: {
      send: typeof storeMocks.send
      loadMessages: typeof storeMocks.loadMessages
      steer: typeof storeMocks.steer
      cancelStream: typeof storeMocks.cancelStream
      cancelSteer: typeof storeMocks.cancelSteer
      appendHistoryMessage: typeof storeMocks.appendHistoryMessage
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
      loadMessages: storeMocks.loadMessages,
      steer: storeMocks.steer,
      cancelStream: storeMocks.cancelStream,
      cancelSteer: storeMocks.cancelSteer,
      appendHistoryMessage: storeMocks.appendHistoryMessage,
      pendingSteers: storeMocks.state.pendingSteers,
      pendingConfirmMap: {},
      pendingAsk: storeMocks.state.pendingAsk,
      isStreaming: storeMocks.state.isStreaming,
      streamingConversationId: storeMocks.state.streamingConversationId,
      cancellingConversationIds: storeMocks.state.cancellingConversationIds,
      runLifecycle: storeMocks.state.runLifecycle,
    }),
  useAttachmentStore: (
    selector: (state: {
      upload: typeof storeMocks.upload
      clear: typeof storeMocks.clear
      hydrate: typeof storeMocks.hydrate
      attachedIds: () => string[]
      staging: Record<string, unknown[]>
    }) => unknown,
  ) => {
    const state = {
      upload: storeMocks.upload,
      clear: storeMocks.clear,
      hydrate: storeMocks.hydrate,
      attachedIds: () => storeMocks.state.attachedIds,
      staging: {},
    }
    const firstSnapshot = selector(state)
    const secondSnapshot = selector(state)
    if (firstSnapshot !== secondSnapshot) {
      throw new Error('attachment selector returned an unstable snapshot')
    }
    return firstSnapshot
  },
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

vi.mock('@/hooks/useMobileMenu', () => ({
  useMobileMenu: (selector: (s: { open: () => void }) => unknown): unknown =>
    selector({ open: vi.fn() }),
}))

vi.mock('@/lib/api/presets', () => ({
  fetchWorkspaceModelPresets: vi.fn().mockResolvedValue([
    { label: 'default', is_default: true },
    { label: 'reasoning', is_default: false },
  ]),
}))

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

describe('InputBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeMocks.cancelSteer.mockResolvedValue(true)
    storeMocks.state.isStreaming = false
    storeMocks.state.streamingConversationId = null
    storeMocks.state.cancellingConversationIds = {}
    storeMocks.state.runLifecycle = {}
    storeMocks.state.pendingAsk = null
    storeMocks.state.pendingSteers = {}
    storeMocks.state.attachedIds = []
    // Reset the per-`wsId` preset selection so each test starts from "no
    // explicit choice / thinking off". The store factory caches a single
    // hook instance per wsId; clearing state on the cached store is safe.
    getPresetSelectionStore('ws-1').setState({
      modelKey: null,
      thinking: 'off',
      presets: [],
      presetFetchStatus: 'idle',
      presetFetchError: null,
    })
  })

  it('keeps the textarea editable once a streamed run is in flight (for steering)', async () => {
    // The real store's send() only resolves when the SSE stream finishes, so
    // the submit handler stays "in flight" for the whole run. Mirror that: send
    // flips streaming on and returns a never-resolving promise.
    storeMocks.send.mockImplementation(() => {
      storeMocks.state.isStreaming = true
      storeMocks.state.streamingConversationId = 'conv-1'
      return new Promise<void>(() => {})
    })

    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')

    fireEvent.change(textarea, { target: { value: 'hello' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(textarea).not.toBeDisabled()
    })
  })

  it('keeps the composer locked while cancellation is still finalizing', () => {
    storeMocks.state.cancellingConversationIds = { 'conv-1': true }

    renderWithIntl(<InputBar conversationId="conv-1" />)

    expect(screen.getByTestId('chat-input')).toBeDisabled()
    expect(screen.getByTestId('chat-input')).toHaveAttribute(
      'placeholder',
      'Previous turn is still finishing…',
    )
    expect(screen.getByTestId('send-button')).toBeDisabled()
  })

  it('routes text to steering while HITL is paused without locking the textarea', async () => {
    storeMocks.state.pendingAsk = { question_id: 'q1' }
    storeMocks.state.runLifecycle = { 'conv-1': 'paused_hitl' }
    storeMocks.steer.mockResolvedValue(true)

    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')

    expect(textarea).not.toBeDisabled()
    expect(textarea).toHaveAttribute('placeholder', 'Add guidance for after the pending decision…')
    fireEvent.change(textarea, { target: { value: 'use the smaller dataset' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.steer).toHaveBeenCalledWith(
        expect.anything(),
        'conv-1',
        'use the smaller dataset',
      )
    })
    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('prepends a rejected steer to text typed while the request was in flight', async () => {
    storeMocks.state.pendingAsk = { question_id: 'q1' }
    storeMocks.state.runLifecycle = { 'conv-1': 'paused_hitl' }
    let rejectSteer: ((reason: Error) => void) | undefined
    storeMocks.steer.mockImplementation(
      () =>
        new Promise<boolean>((_resolve, reject) => {
          rejectSteer = reject
        }),
    )

    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: 'submitted' } })
    fireEvent.click(screen.getByTestId('send-button'))
    expect(textarea).toHaveValue('')
    fireEvent.change(textarea, { target: { value: 'new typing' } })
    rejectSteer?.(new Error('queue rejected'))

    await waitFor(() => expect(textarea).toHaveValue('submitted\nnew typing'))
  })

  it('prepends a rejected send to text typed while the request was in flight', async () => {
    let rejectSend: ((reason: Error) => void) | undefined
    storeMocks.send.mockImplementation(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectSend = reject
        }),
    )

    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: 'submitted' } })
    fireEvent.click(screen.getByTestId('send-button'))
    expect(textarea).toHaveValue('')
    fireEvent.change(textarea, { target: { value: 'new typing' } })
    rejectSend?.(new Error('send rejected'))

    await waitFor(() => expect(textarea).toHaveValue('submitted\nnew typing'))
  })

  it('restores failed steering text ahead of newer composer text', async () => {
    storeMocks.state.pendingSteers = {
      'conv-1': [
        {
          steerId: 'failed-steer',
          text: 'failed guidance in full',
          state: 'failed',
          createdAt: '2026-08-12T00:00:00.000Z',
        },
      ],
    }
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: 'newer typing' } })

    fireEvent.click(screen.getByRole('button', { name: /restore/i }))

    await waitFor(() => expect(textarea).toHaveValue('failed guidance in full\nnewer typing'))
  })

  it('restores the draft and shows a friendly message on an active-run conflict', async () => {
    storeMocks.send.mockRejectedValue(
      new ApiError(
        'Conversation conv-1 already has an active run',
        409,
        'active_run_conflict',
        null,
      ),
    )
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')

    fireEvent.change(textarea, { target: { value: 'keep my draft' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(textarea).toHaveValue('keep my draft')
    })
    expect(toast.error).toHaveBeenCalledWith('Previous turn is still finishing. Try again shortly.')
  })

  it('restores the draft when a stale-send steering reroute fails', async () => {
    storeMocks.send.mockRejectedValue(
      new ApiError('Steering queue is full', 429, 'steer_queue_full', null),
    )
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')

    fireEvent.change(textarea, { target: { value: 'keep this guidance' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => expect(textarea).toHaveValue('keep this guidance'))
  })

  it('refreshes message state as well as attachments after an attachment conflict', async () => {
    storeMocks.state.attachedIds = ['file-1']
    storeMocks.send.mockRejectedValue(
      new ApiError('Conversation already has an active run', 409, 'active_run_conflict', null),
    )
    renderWithIntl(<InputBar conversationId="conv-1" />)

    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'with attachment' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.loadMessages).toHaveBeenCalledWith(expect.anything(), 'conv-1', {
        force: true,
        preserveOtherConversationStream: true,
      })
    })
    expect(storeMocks.hydrate).toHaveBeenCalledTimes(2)
  })

  it('keeps attachment selector snapshots stable', () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
  })

  it('focuses the textarea when clicking the visible input shell padding', () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)

    const textarea = screen.getByTestId('chat-input')
    const shell = textarea.parentElement

    expect(shell).toBeInstanceOf(HTMLElement)
    fireEvent.mouseDown(shell!)

    expect(document.activeElement).toBe(textarea)
  })

  it('keeps the file input out of the visible input shell hit area', () => {
    const { container } = renderWithIntl(<InputBar conversationId="conv-1" />)

    const textarea = screen.getByTestId('chat-input')
    const shell = textarea.parentElement
    const fileInput = container.querySelector('input[type="file"]')

    expect(shell).toBeInstanceOf(HTMLElement)
    expect(fileInput).toBeInstanceOf(HTMLInputElement)
    expect(fileInput).toHaveAttribute('hidden')
    expect(shell).not.toContainElement(fileInput)
  })

  it('stages files on the new chat input and passes them to onSubmit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const { container } = renderWithIntl(<InputBar onSubmit={onSubmit} />)
    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' })

    expect(screen.getByTestId('composer-add-menu')).not.toBeDisabled()

    const fileInput = container.querySelector('input[type="file"]')
    expect(fileInput).toBeInstanceOf(HTMLInputElement)

    fireEvent.change(fileInput!, { target: { files: [file] } })

    expect(screen.getByText('hello.txt')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith('', [file])
    })
  })

  it('renders the model picker in the toolbar when a workspace is present', () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    expect(screen.getByRole('button', { name: 'Model and thinking effort' })).toBeInTheDocument()
  })

  it('forwards the current model_key and reasoning selection on send', async () => {
    getPresetSelectionStore('ws-1').setState({
      modelKey: 'reasoning',
      thinking: 'medium',
      // The send validates the key against the loaded presets, so a forwarded
      // key must exist in the list (a stale key would coerce to null).
      presets: [
        { key: 'reasoning', kind: 'custom', primary: 'p/m', description: '', is_default: false },
      ],
    })
    storeMocks.send.mockResolvedValue(undefined)

    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hello' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalled()
    })
    const callArgs = storeMocks.send.mock.calls[0]
    // send(client, conversationId, text, ids, optimisticAttachments, options)
    expect(callArgs[1]).toBe('conv-1')
    expect(callArgs[2]).toBe('hello')
    expect(callArgs[5]).toEqual({
      model_key: 'reasoning',
      reasoning: { mode: 'on', effort: 'medium', summary: 'none' },
    })
  })

  it('blocks send after a successful preset fetch confirms there are no usable presets', async () => {
    storeMocks.send.mockResolvedValue(undefined)

    renderWithIntl(<InputBar conversationId="conv-1" />)
    await waitFor(() => {
      expect(getPresetSelectionStore('ws-1').getState().presetFetchStatus).toBe('ready')
    })
    getPresetSelectionStore('ws-1').setState({
      presets: [],
      presetFetchStatus: 'ready',
      presetFetchError: null,
    })

    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: 'hello' } })
    await waitFor(() => expect(screen.getByTestId('send-button')).toBeDisabled())
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('does not block send while the first preset request is still loading', async () => {
    storeMocks.send.mockResolvedValue(undefined)

    renderWithIntl(<InputBar conversationId="conv-1" />)
    getPresetSelectionStore('ws-1').setState({
      presets: [],
      presetFetchStatus: 'loading',
      presetFetchError: null,
    })

    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hello' } })
    await waitFor(() => expect(screen.getByTestId('send-button')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => expect(storeMocks.send).toHaveBeenCalled())
  })

  it('blocks send while the conversation model is still syncing', () => {
    storeMocks.send.mockResolvedValue(undefined)

    renderWithIntl(<InputBar conversationId="conv-1" modelSyncPending />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hello' } })

    // The send button is disabled until sync completes, and clicking it (or
    // hitting Enter) must not start a turn with the prior conversation's model.
    expect(screen.getByTestId('send-button')).toBeDisabled()
    fireEvent.click(screen.getByTestId('send-button'))
    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('sends model_key: null when the user has not picked a model', async () => {
    storeMocks.send.mockResolvedValue(undefined)
    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hi' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalled()
    })
    expect(storeMocks.send.mock.calls[0][5]).toEqual({
      model_key: null,
      reasoning: { mode: 'off', effort: 'minimal', summary: 'none' },
    })
  })

  it('creates a draft conversation on first file pick when onCreateConversation is provided', async () => {
    const onCreateConversation = vi.fn().mockResolvedValue('conv-1')
    const onSubmit = vi.fn()
    const { container } = renderWithIntl(
      <InputBar onCreateConversation={onCreateConversation} onSubmit={onSubmit} />,
    )
    const fileInput = container.querySelector('input[type="file"]')
    expect(fileInput).toBeInstanceOf(HTMLInputElement)
    const file = new File(['x'], 'a.txt', { type: 'text/plain' })

    fireEvent.change(fileInput!, { target: { files: [file] } })

    await waitFor(() => {
      expect(onCreateConversation).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(storeMocks.upload).toHaveBeenCalledWith(expect.anything(), 'conv-1', [file])
    })
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
