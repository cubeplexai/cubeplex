import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import { InputBar } from '../../components/layout/InputBar'

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
