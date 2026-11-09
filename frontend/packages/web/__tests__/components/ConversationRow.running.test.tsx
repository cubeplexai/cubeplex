import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Conversation } from '@cubeplex/core'
import en from '../../messages/en.json'
import { ConversationRow } from '../../components/layout/Sidebar'

const navFallback = vi.hoisted(() => ({
  scheduleConversationNavFallback: vi.fn(),
}))

vi.mock('@/lib/conversationNavFallback', () => navFallback)

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
    prefetch,
    onClick,
    ...rest
  }: {
    children: React.ReactNode
    href: string
    prefetch?: boolean
    onClick?: (event: React.MouseEvent<HTMLAnchorElement>) => void
    [key: string]: unknown
  }) => (
    <a
      href={href}
      data-prefetch={prefetch === undefined ? 'default' : String(prefetch)}
      onClick={(event) => {
        onClick?.(event)
        // Next's Link prevents the native navigation after the click handler.
        event.preventDefault()
      }}
      {...rest}
    >
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

  it('does not prefetch, and schedules a fallback load on a plain title click', () => {
    renderRow(makeConvo({ id: 'c2', title: 'Beta chat' }))

    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/w/ws-1/conversations/c2')
    expect(link).toHaveAttribute('data-prefetch', 'false')

    fireEvent.click(link)

    expect(storeMocks.setActive).toHaveBeenCalledWith('c2')
    expect(navFallback.scheduleConversationNavFallback).toHaveBeenCalledWith(
      '/w/ws-1/conversations/c2',
      window.location.pathname,
    )
  })

  it('still highlights a modified click without scheduling a fallback load', () => {
    renderRow(makeConvo({ id: 'c2', title: 'Beta chat' }))

    fireEvent.click(screen.getByRole('link'), { metaKey: true })

    expect(storeMocks.setActive).toHaveBeenCalledWith('c2')
    expect(navFallback.scheduleConversationNavFallback).not.toHaveBeenCalled()
  })

  it('does not open the conversation from the row menu', () => {
    renderRow(makeConvo({ id: 'c2', title: 'Beta chat' }))

    fireEvent.click(screen.getByLabelText(en.sidebar.moreActions))

    expect(storeMocks.setActive).not.toHaveBeenCalled()
    expect(navFallback.scheduleConversationNavFallback).not.toHaveBeenCalled()
  })

  it('does not schedule a fallback when the row has no workspace', () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ConversationRow
          convo={makeConvo({ id: 'c2', title: 'Beta chat' })}
          isActive={false}
          currentWsId={null}
        />
      </NextIntlClientProvider>,
    )

    fireEvent.click(screen.getByRole('link'))

    expect(navFallback.scheduleConversationNavFallback).not.toHaveBeenCalled()
  })
})
