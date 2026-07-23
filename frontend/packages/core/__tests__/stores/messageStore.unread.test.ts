import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isAwayFromConversation, useMessageStore } from '../../src/stores/messageStore'
import { useConversationStore } from '../../src/stores/conversationStore'
import { useAuthStore } from '../../src/stores/authStore'
import { loadUnreadMap, unreadStorageKey } from '../../src/stores/conversationUnreadSync'
import type { AgentStream } from '../../src/stores/messageStore'
import type { MeResult } from '../../src/api/auth'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    cancelActiveRun: vi.fn().mockResolvedValue({ status: 'cancelled', run_id: 'r1' }),
  }
})

import { cancelActiveRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never
const USER_A = 'user_a'
const USER_B = 'user_b'

function setUser(id: string | null): void {
  useAuthStore.setState({
    user: id
      ? ({
          id,
          email: `${id}@example.com`,
          display_name: id,
          avatar_url: null,
          avatar_seed: null,
          avatar_kind: null,
          avatar_style: null,
          language: 'en',
          is_verified: true,
        } satisfies MeResult)
      : null,
  })
}

function seedStreaming(conversationId: string, stream: Partial<AgentStream> = {}): void {
  useMessageStore.setState({
    messages: { [conversationId]: [] },
    streamAgents: {
      main: {
        text: '',
        toolCalls: [],
        toolResults: [],
        thinking: '',
        blocks: [],
        name: null,
        ...stream,
      },
    },
    isStreaming: true,
    streamingConversationId: conversationId,
    currentRunId: 'r1',
    unreadConversationIds: {},
  })
}

describe('messageStore unread', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useConversationStore.setState({ activeId: null })
    setUser(USER_A)
    useMessageStore.setState({
      messages: {},
      streamAgents: {},
      isStreaming: false,
      streamingConversationId: null,
      currentRunId: null,
      unreadConversationIds: {},
      pendingConfirmMap: {},
      pendingAsk: null,
      pendingSteers: {},
      errors: {},
    })
  })

  afterEach(() => {
    localStorage.clear()
    setUser(null)
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
  })

  it('markUnread / clearUnread publish to user-scoped localStorage', () => {
    useMessageStore.getState().markUnread('c1')
    useMessageStore.getState().markUnread('c1')
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ c1: true })
    expect(loadUnreadMap(USER_A)).toEqual({ c1: true })
    expect(localStorage.getItem(unreadStorageKey(USER_B))).toBeNull()

    useMessageStore.getState().clearUnread('c1')
    useMessageStore.getState().clearUnread('c1')
    expect(useMessageStore.getState().unreadConversationIds).toEqual({})
    expect(loadUnreadMap(USER_A)).toEqual({})
  })

  it('marks unread when stream completes while activeId is another conversation', async () => {
    seedStreaming('cA', {
      text: 'done',
      blocks: [{ type: 'text', text: 'done' }],
    })
    useConversationStore.setState({ activeId: 'cB' })

    await useMessageStore.getState().cancelStream(fakeClient, 'cA')

    expect(useMessageStore.getState().unreadConversationIds.cA).toBe(true)
    expect(cancelActiveRun).toHaveBeenCalled()
  })

  it('does not mark unread when user is viewing the same conversation', async () => {
    seedStreaming('cA', {
      text: 'done',
      blocks: [{ type: 'text', text: 'done' }],
    })
    useConversationStore.setState({ activeId: 'cA' })

    await useMessageStore.getState().cancelStream(fakeClient, 'cA')

    expect(useMessageStore.getState().unreadConversationIds.cA).toBeUndefined()
  })

  it('marks unread on empty cancel while away', async () => {
    seedStreaming('cA', {})
    useConversationStore.setState({ activeId: 'cB' })

    await useMessageStore.getState().cancelStream(fakeClient, 'cA')

    expect(useMessageStore.getState().unreadConversationIds.cA).toBe(true)
  })

  it('isAwayFromConversation tracks activeId and visibility', () => {
    useConversationStore.setState({ activeId: 'cA' })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
    expect(isAwayFromConversation('cA')).toBe(false)
    expect(isAwayFromConversation('cB')).toBe(true)

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    })
    expect(isAwayFromConversation('cA')).toBe(true)
  })

  it('marks unread when document is hidden even if activeId matches', async () => {
    seedStreaming('cA', {
      text: 'done',
      blocks: [{ type: 'text', text: 'done' }],
    })
    useConversationStore.setState({ activeId: 'cA' })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    })

    await useMessageStore.getState().cancelStream(fakeClient, 'cA')

    expect(useMessageStore.getState().unreadConversationIds.cA).toBe(true)
  })

  it('remote mark is rejected when this tab is focused on the conversation', () => {
    useConversationStore.setState({ activeId: 'cA' })
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })

    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cA', at: 100 })
    expect(useMessageStore.getState().unreadConversationIds.cA).toBeUndefined()
  })

  it('remote mark applies when away', () => {
    useConversationStore.setState({ activeId: 'cB' })
    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cA', at: 100 })
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ cA: true })
  })

  it('remote clear removes only the target id', () => {
    useMessageStore.setState({ unreadConversationIds: { cA: true, cB: true } })
    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cA', at: 50 })
    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cB', at: 50 })
    useMessageStore
      .getState()
      .__applyUnreadRemote({ type: 'clear', conversationId: 'cA', before: 50 })
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ cB: true })
  })

  it('stale remote clear does not wipe a newer mark', () => {
    useConversationStore.setState({ activeId: 'cB' })
    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cA', at: 100 })
    // Newer mark
    useMessageStore.getState().__applyUnreadRemote({ type: 'mark', conversationId: 'cA', at: 200 })
    // Delayed clear for the older mark
    useMessageStore
      .getState()
      .__applyUnreadRemote({ type: 'clear', conversationId: 'cA', before: 100 })
    expect(useMessageStore.getState().unreadConversationIds.cA).toBe(true)
  })

  it('merges pending in-memory marks when auth user binds', () => {
    setUser(null)
    useMessageStore.setState({ unreadConversationIds: {} })
    useMessageStore.getState().markUnread('pending1')
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ pending1: true })

    setUser(USER_A)
    expect(useMessageStore.getState().unreadConversationIds.pending1).toBe(true)
    expect(loadUnreadMap(USER_A).pending1).toBe(true)
  })

  it('resetUnread clears memory and optionally storage', () => {
    useMessageStore.getState().markUnread('c1')
    expect(loadUnreadMap(USER_A)).toEqual({ c1: true })
    useMessageStore.getState().resetUnread({ clearStorage: true })
    expect(useMessageStore.getState().unreadConversationIds).toEqual({})
    expect(loadUnreadMap(USER_A)).toEqual({})
  })

  it('rebinds storage when auth user changes', () => {
    useMessageStore.getState().markUnread('cA')
    expect(loadUnreadMap(USER_A)).toEqual({ cA: true })

    // User B starts with empty scoped storage.
    setUser(USER_B)
    // Auth subscribe is async in the sense it runs synchronously via zustand.
    // rebind loads empty map for B.
    expect(useMessageStore.getState().unreadConversationIds.cA).toBeUndefined()

    useMessageStore.getState().markUnread('cB')
    expect(loadUnreadMap(USER_B)).toEqual({ cB: true })
    expect(loadUnreadMap(USER_A)).toEqual({ cA: true })
  })
})
