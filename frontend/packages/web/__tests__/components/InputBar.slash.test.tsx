import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import en from '../../messages/en.json'
import { InputBar } from '../../components/layout/InputBar'
import { getPresetSelectionStore } from '../../lib/stores/preset-selection'

const storeMocks = vi.hoisted(() => ({
  send: vi.fn(),
  steer: vi.fn(),
  cancelStream: vi.fn().mockResolvedValue(undefined),
  cancelSteer: vi.fn(),
  upload: vi.fn(),
  clear: vi.fn(),
  hydrate: vi.fn(),
  setWorkspaceId: vi.fn(),
  compactConversation: vi.fn().mockResolvedValue({ ok: true, compacted: true }),
  state: { isStreaming: false, streamingConversationId: null as string | null },
}))

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  useRouter: () => routerMocks,
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), message: vi.fn(), error: vi.fn() },
}))

vi.mock('@cubeplex/core', () => ({
  createApiClient: () => ({
    setWorkspaceId: storeMocks.setWorkspaceId,
  }),
  compactConversation: storeMocks.compactConversation,
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
  ) => {
    const state = {
      upload: storeMocks.upload,
      clear: storeMocks.clear,
      hydrate: storeMocks.hydrate,
      attachedIds: () => [],
      staging: {},
    }
    return selector(state)
  },
}))

vi.mock('@/hooks/useWorkspaceContext', () => ({
  useWorkspaceContext: () => ({ workspaceId: 'ws-1' }),
}))

vi.mock('@/lib/api/presets', () => ({
  fetchWorkspaceModelPresets: vi.fn().mockResolvedValue([
    {
      label: 'default',
      is_default: true,
      key: 'default',
      kind: 'tier',
      primary: 'x',
      description: '',
    },
  ]),
}))

function renderWithIntl(ui: React.ReactElement): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      {ui}
    </NextIntlClientProvider>,
  )
}

describe('InputBar slash commands', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeMocks.state.isStreaming = false
    storeMocks.state.streamingConversationId = null
    getPresetSelectionStore('ws-1').setState({ modelKey: null, thinking: 'off' })
  })

  it('opens the palette when typing /', async () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/' } })
    expect(await screen.findByTestId('slash-command-popover')).toBeInTheDocument()
    expect(screen.getByTestId('slash-cmd-new')).toBeInTheDocument()
    expect(screen.getByTestId('slash-cmd-skills')).toBeInTheDocument()
  })

  it('runs /stop while streaming without send/steer', async () => {
    storeMocks.state.isStreaming = true
    storeMocks.state.streamingConversationId = 'conv-1'
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/stop' } })
    fireEvent.click(await screen.findByTestId('slash-cmd-stop'))
    await waitFor(() => {
      expect(storeMocks.cancelStream).toHaveBeenCalled()
    })
    expect(storeMocks.send).not.toHaveBeenCalled()
    expect(storeMocks.steer).not.toHaveBeenCalled()
    expect(textarea).toHaveValue('')
  })

  it('navigates on /skills without send', async () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/skills' } })
    fireEvent.click(await screen.findByTestId('slash-cmd-skills'))
    await waitFor(() => {
      expect(routerMocks.push).toHaveBeenCalledWith('/w/ws-1/skills')
    })
    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('calls compact on /compact when idle', async () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/compact' } })
    fireEvent.click(await screen.findByTestId('slash-cmd-compact'))
    await waitFor(() => {
      expect(storeMocks.compactConversation).toHaveBeenCalled()
    })
    expect(storeMocks.send).not.toHaveBeenCalled()
  })

  it('hides /compact while streaming', async () => {
    storeMocks.state.isStreaming = true
    storeMocks.state.streamingConversationId = 'conv-1'
    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: '/comp' } })
    await screen.findByTestId('slash-command-popover')
    expect(screen.queryByTestId('slash-cmd-compact')).not.toBeInTheDocument()
  })

  it('sends unknown /text as plain text when idle', async () => {
    storeMocks.send.mockResolvedValue(undefined)
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/zzzzz' } })
    await screen.findByTestId('slash-command-popover')
    fireEvent.keyDown(textarea, { key: 'Enter' })
    await waitFor(() => {
      expect(storeMocks.send).toHaveBeenCalled()
    })
  })

  it('does not open palette for /foo bar', () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: '/foo bar' } })
    expect(screen.queryByTestId('slash-command-popover')).not.toBeInTheDocument()
  })

  it('Esc dismisses without send', async () => {
    renderWithIntl(<InputBar conversationId="conv-1" />)
    const textarea = screen.getByTestId('chat-input')
    fireEvent.change(textarea, { target: { value: '/new' } })
    await screen.findByTestId('slash-command-popover')
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(screen.queryByTestId('slash-command-popover')).not.toBeInTheDocument()
    expect(storeMocks.send).not.toHaveBeenCalled()
    expect(textarea).toHaveValue('/new')
  })

  it('/new while streaming does not cancelStream', async () => {
    storeMocks.state.isStreaming = true
    storeMocks.state.streamingConversationId = 'conv-1'
    renderWithIntl(<InputBar conversationId="conv-1" />)
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: '/new' } })
    fireEvent.click(await screen.findByTestId('slash-cmd-new'))
    await waitFor(() => {
      expect(routerMocks.push).toHaveBeenCalledWith('/w/ws-1')
    })
    expect(storeMocks.cancelStream).not.toHaveBeenCalled()
  })
})
