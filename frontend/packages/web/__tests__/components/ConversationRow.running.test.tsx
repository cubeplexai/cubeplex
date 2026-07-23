import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Conversation } from '@cubeplex/core'
import en from '../../messages/en.json'
import { ConversationRow } from '../../components/layout/Sidebar'

const storeMocks = vi.hoisted(() => ({
  isStreaming: false,
  streamingConversationId: null as string | null,
  unreadConversationIds: {} as Record<string, true>,
  remove: vi.fn(),
  rename: vi.fn(),
  setPin: vi.fn(),
  setActive: vi.fn(),
  pinPending: {} as Record<string, boolean>,
  conversationParticipants: {} as Record<string, unknown[]>,
}))

vi.mock('next/link', () => ({
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode
    href: string
    [key: string]: unknown
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}))

// DeleteConversationDialog (rendered by ConversationRow) uses the app router.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/w/ws-1/conversations/c1',
}))

vi.mock('@cubeplex/core', () => {
  const useMessageStore = (
    selector: (state: {
      isStreaming: boolean
      streamingConversationId: string | null
      unreadConversationIds: Record<string, true>
    }) => unknown,
  ) =>
    selector({
      isStreaming: storeMocks.isStreaming,
      streamingConversationId: storeMocks.streamingConversationId,
      unreadConversationIds: storeMocks.unreadConversationIds,
    })
  useMessageStore.getState = () => ({
    clearUnread: vi.fn(),
    isStreaming: storeMocks.isStreaming,
    streamingConversationId: storeMocks.streamingConversationId,
    unreadConversationIds: storeMocks.unreadConversationIds,
  })

  return {
    createApiClient: () => ({
      setWorkspaceId: vi.fn(),
    }),
    useMessageStore,
    useConversationStore: (
      selector?: (state: {
        remove: typeof storeMocks.remove
        rename: typeof storeMocks.rename
        setPin: typeof storeMocks.setPin
        setActive: typeof storeMocks.setActive
        pinPending: Record<string, boolean>
        conversationParticipants: Record<string, unknown[]>
      }) => unknown,
    ) => {
      const state = {
        remove: storeMocks.remove,
        rename: storeMocks.rename,
        setPin: storeMocks.setPin,
        setActive: storeMocks.setActive,
        pinPending: storeMocks.pinPending,
        conversationParticipants: storeMocks.conversationParticipants,
      }
      // ConversationRow both destructures and selects.
      if (typeof selector === 'function') return selector(state)
      return state
    },
  }
})

function makeConvo(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1',
    title: 'Alpha chat',
    is_pinned: false,
    topic_id: null,
    is_group_chat: false,
    created_at: '2026-07-22T00:00:00Z',
    updated_at: '2026-07-22T00:00:00Z',
    model_key: null,
    reasoning: { mode: 'off', effort: 'medium', summary: 'auto' },
    ...overrides,
  }
}

function renderRow(convo: Conversation = makeConvo()): ReturnType<typeof render> {
  return render(
    <NextIntlClientProvider locale="en" messages={en}>
      <ConversationRow convo={convo} isActive={false} currentWsId="ws-1" />
    </NextIntlClientProvider>,
  )
}

describe('ConversationRow running indicator', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeMocks.isStreaming = false
    storeMocks.streamingConversationId = null
    storeMocks.unreadConversationIds = {}
    storeMocks.pinPending = {}
    storeMocks.conversationParticipants = {}
  })

  it('shows a spinner with accessible name when this conversation is streaming', () => {
    storeMocks.isStreaming = true
    storeMocks.streamingConversationId = 'c1'

    renderRow(makeConvo({ id: 'c1', title: 'Alpha chat' }))

    expect(screen.getByLabelText(en.sidebar.conversationRunning)).toBeInTheDocument()
    expect(screen.getByText('Alpha chat')).toBeInTheDocument()
    // Menu trigger still present — row chrome is intact
    expect(screen.getByLabelText(en.sidebar.moreActions)).toBeInTheDocument()
  })

  it('does not show a spinner for a different conversation while another streams', () => {
    storeMocks.isStreaming = true
    storeMocks.streamingConversationId = 'c1'

    renderRow(makeConvo({ id: 'c2', title: 'Beta chat' }))

    expect(screen.queryByLabelText(en.sidebar.conversationRunning)).not.toBeInTheDocument()
    expect(screen.getByText('Beta chat')).toBeInTheDocument()
  })

  it('does not show a spinner when idle', () => {
    storeMocks.isStreaming = false
    storeMocks.streamingConversationId = null

    renderRow()

    expect(screen.queryByLabelText(en.sidebar.conversationRunning)).not.toBeInTheDocument()
  })

  it('does not show a spinner for paused HITL (isStreaming false, id still set)', () => {
    storeMocks.isStreaming = false
    storeMocks.streamingConversationId = 'c1'

    renderRow(makeConvo({ id: 'c1' }))

    expect(screen.queryByLabelText(en.sidebar.conversationRunning)).not.toBeInTheDocument()
  })
})
