import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isAwayFromConversation, useMessageStore } from '../../src/stores/messageStore'
import { useConversationStore } from '../../src/stores/conversationStore'
import {
  UNREAD_STORAGE_KEY,
  loadUnreadMap,
  parseUnreadMap,
  publishUnreadMap,
  unreadMapsEqual,
} from '../../src/stores/conversationUnreadSync'
import type { AgentStream } from '../../src/stores/messageStore'

vi.mock('../../src/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api')>()
  return {
    ...actual,
    cancelActiveRun: vi.fn().mockResolvedValue({ status: 'cancelled', run_id: 'r1' }),
  }
})

import { cancelActiveRun } from '../../src/api'

const fakeClient = { resolvePath: (s: string) => s, post: vi.fn() } as never

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

describe('conversationUnreadSync helpers', () => {
  it('parseUnreadMap accepts only true-valued id keys', () => {
    expect(parseUnreadMap(null)).toEqual({})
    expect(parseUnreadMap('{"a":true,"b":false,"c":1}')).toEqual({ a: true })
    expect(parseUnreadMap('not-json')).toEqual({})
  })

  it('unreadMapsEqual compares key sets', () => {
    expect(unreadMapsEqual({ a: true }, { a: true })).toBe(true)
    expect(unreadMapsEqual({ a: true }, { b: true })).toBe(false)
    expect(unreadMapsEqual({ a: true, b: true }, { a: true })).toBe(false)
  })
})

describe('messageStore unread', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    useConversationStore.setState({ activeId: null })
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
  })

  it('markUnread / clearUnread are idempotent and publish to localStorage', () => {
    useMessageStore.getState().markUnread('c1')
    useMessageStore.getState().markUnread('c1')
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ c1: true })
    expect(loadUnreadMap()).toEqual({ c1: true })

    useMessageStore.getState().clearUnread('c1')
    useMessageStore.getState().clearUnread('c1')
    expect(useMessageStore.getState().unreadConversationIds).toEqual({})
    expect(loadUnreadMap()).toEqual({})
  })

  it('__applyUnreadMap replaces local state without requiring localStorage write loop', () => {
    useMessageStore.getState().markUnread('c1')
    useMessageStore.getState().__applyUnreadMap({ c2: true })
    expect(useMessageStore.getState().unreadConversationIds).toEqual({ c2: true })
  })

  it('marks unread when stream completes while activeId is another conversation', async () => {
    seedStreaming('cA', {
      text: 'done',
      blocks: [{ type: 'text', text: 'done' }],
    })
    useConversationStore.setState({ activeId: 'cB' })

    // Drive terminalization via cancelStream (finalizeCompletedStream path).
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

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
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

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
  })

  it('publishUnreadMap writes storage used by multi-tab peers', () => {
    publishUnreadMap({ x: true })
    expect(localStorage.getItem(UNREAD_STORAGE_KEY)).toBe(JSON.stringify({ x: true }))
    expect(loadUnreadMap()).toEqual({ x: true })
  })
})
