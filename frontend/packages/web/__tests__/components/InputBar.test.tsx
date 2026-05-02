import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, beforeEach, vi } from 'vitest'
import en from '../../messages/en.json'
import { InputBar } from '../../components/layout/InputBar'

const storeMocks = vi.hoisted(() => ({
  send: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  hydrate: vi.fn(),
  setWorkspaceId: vi.fn(),
}))

vi.mock('@cubebox/core', () => ({
  createApiClient: () => ({
    setWorkspaceId: storeMocks.setWorkspaceId,
  }),
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
})
