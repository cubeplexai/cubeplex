import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import { InputBar } from '../../components/layout/InputBar'
import { getPresetSelectionStore } from '../../lib/stores/preset-selection'

const storeMocks = vi.hoisted(() => ({
  send: vi.fn(),
  steer: vi.fn(),
  cancelStream: vi.fn(),
  cancelSteer: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  hydrate: vi.fn(),
  setWorkspaceId: vi.fn(),
  state: { isStreaming: false, streamingConversationId: null as string | null },
}))

vi.mock('@cubebox/core', () => ({
  createApiClient: () => ({
    setWorkspaceId: storeMocks.setWorkspaceId,
  }),
  useMessageStore: (
    selector: (state: {
      send: typeof storeMocks.send
      steer: typeof storeMocks.steer
      cancelStream: typeof storeMocks.cancelStream
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
      steer: storeMocks.steer,
      cancelStream: storeMocks.cancelStream,
      cancelSteer: storeMocks.cancelSteer,
      pendingSteers: {},
      pendingConfirmMap: {},
      pendingAsk: null,
      isStreaming: storeMocks.state.isStreaming,
      streamingConversationId: storeMocks.state.streamingConversationId,
    }),
  useAttachmentStore: (
    selector: (state: {
      upload: typeof storeMocks.upload
      clear: typeof storeMocks.clear
      hydrate: typeof storeMocks.hydrate
      attachedIds: () => string[]
      staging: Record<string, unknown[]>
    }) => unknown,
  ) =>
    selector({
      upload: storeMocks.upload,
      clear: storeMocks.clear,
      hydrate: storeMocks.hydrate,
      attachedIds: () => [],
      staging: {},
    }),
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
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
    storeMocks.state.isStreaming = false
    storeMocks.state.streamingConversationId = null
    // Reset the per-`wsId` preset selection so each test starts from "no
    // explicit choice / thinking off". The store factory caches a single
    // hook instance per wsId; clearing state on the cached store is safe.
    getPresetSelectionStore('ws-1').setState({ modelPresetKey: null, thinking: 'off' })
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

    expect(screen.getByRole('button', { name: 'Attach files' })).not.toBeDisabled()

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

  it('forwards the current preset_label and thinking selection on send', async () => {
    getPresetSelectionStore('ws-1').setState({ modelPresetKey: 'reasoning', thinking: 'medium' })
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
    expect(callArgs[5]).toEqual({ preset_label: 'reasoning', thinking: 'medium' })
  })

  it('sends preset_label: null when the user has not picked a preset', async () => {
    storeMocks.send.mockResolvedValue(undefined)
    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'hi' } })
    fireEvent.click(screen.getByTestId('send-button'))

    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalled()
    })
    expect(storeMocks.send.mock.calls[0][5]).toEqual({ preset_label: null, thinking: 'off' })
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
