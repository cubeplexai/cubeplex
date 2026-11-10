import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SenderBadge } from '@/components/chat/SenderBadge'
import type { ConversationParticipant, TopicParticipant, User } from '@cubeplex/core'

const storeMocks = vi.hoisted(() => ({
  currentUser: null as User | null,
  conversationParticipants: {} as Record<string, ConversationParticipant[]>,
  topicParticipants: {} as Record<string, TopicParticipant[]>,
}))

vi.mock('@cubeplex/core', () => ({
  useAuthStore: (selector: (state: { user: User | null }) => unknown) =>
    selector({ user: storeMocks.currentUser }),
  useConversationStore: (
    selector: (state: {
      conversationParticipants: Record<string, ConversationParticipant[]>
    }) => unknown,
  ) => selector({ conversationParticipants: storeMocks.conversationParticipants }),
  useTopicStore: (
    selector: (state: { topicParticipants: Record<string, TopicParticipant[]> }) => unknown,
  ) => selector({ topicParticipants: storeMocks.topicParticipants }),
}))

describe('SenderBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    storeMocks.currentUser = null
    storeMocks.conversationParticipants = {}
    storeMocks.topicParticipants = {}
  })

  it('renders display name and user avatar for current user with uploaded avatar_url', () => {
    storeMocks.currentUser = {
      id: 'user-me',
      email: 'me@example.com',
      display_name: 'Alice Me',
      avatar_url: 'https://example.com/alice.png',
      avatar_seed: 'seed-alice',
      avatar_style: 'micah',
    } as unknown as User

    const { container } = render(
      <SenderBadge userId="user-me" displayName="Alice Me" conversationId="conv-1" />,
    )

    expect(screen.getByText('Alice Me')).toBeInTheDocument()
    const img = container.querySelector('img[src="https://example.com/alice.png"]')
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('alt', 'Alice Me')
  })

  it('renders display name and participant avatar for another conversation member', () => {
    storeMocks.currentUser = {
      id: 'user-me',
      email: 'me@example.com',
    } as unknown as User

    storeMocks.conversationParticipants = {
      'conv-1': [
        {
          id: 'cp-1',
          conversation_id: 'conv-1',
          user_id: 'user-bob',
          joined_at: '2026-07-01T00:00:00Z',
          display_name: 'Bob Other',
          avatar_url: '/api/v1/avatar/user-bob?v=123456',
          avatar_seed: 'seed-bob',
          avatar_style: 'notionists',
        },
      ],
    }

    const { container } = render(
      <SenderBadge userId="user-bob" displayName="Bob Other" conversationId="conv-1" />,
    )

    expect(screen.getByText('Bob Other')).toBeInTheDocument()
    const img = container.querySelector('img[src="/api/v1/avatar/user-bob?v=123456"]')
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('alt', 'Bob Other')
  })

  it('renders display name and participant avatar from topicParticipants when in topic conversation', () => {
    storeMocks.currentUser = {
      id: 'user-me',
      email: 'me@example.com',
    } as unknown as User

    storeMocks.topicParticipants = {
      'top-1': [
        {
          id: 'tp-1',
          topic_id: 'top-1',
          user_id: 'user-charlie',
          joined_at: '2026-07-01T00:00:00Z',
          role: 'member',
          display_name: 'Charlie Topic',
          avatar_url: '/api/v1/avatar/user-charlie?v=789',
          avatar_seed: 'seed-charlie',
          avatar_style: 'bottts',
        },
      ],
    }

    const { container } = render(
      <SenderBadge
        userId="user-charlie"
        displayName="Charlie Topic"
        conversationId="conv-topic-1"
        topicId="top-1"
      />,
    )

    expect(screen.getByText('Charlie Topic')).toBeInTheDocument()
    const img = container.querySelector('img[src="/api/v1/avatar/user-charlie?v=789"]')
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('alt', 'Charlie Topic')
  })

  it('falls back to userId and generated avatar when participant not found in store', () => {
    storeMocks.currentUser = {
      id: 'user-me',
      email: 'me@example.com',
    } as unknown as User

    const { container } = render(
      <SenderBadge userId="user-unknown" displayName="Stranger" conversationId="conv-1" />,
    )

    expect(screen.getByText('Stranger')).toBeInTheDocument()
    const avatarSlot = container.querySelector('[data-slot="avatar"]')
    expect(avatarSlot).toBeInTheDocument()
    // No real avatar image rendered, only generated SVG data URI image
    const realImg = container.querySelector('img[src^="http"], img[src^="/api/"]')
    expect(realImg).toBeNull()
  })
})
