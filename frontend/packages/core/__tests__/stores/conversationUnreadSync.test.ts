import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  UNREAD_STORAGE_KEY_LEGACY,
  dropLegacyUnreadStorage,
  loadUnreadMap,
  parseUnreadMap,
  publishClear,
  publishMark,
  saveUnreadMap,
  subscribeUnreadSync,
  unreadStorageKey,
} from '../../src/stores/conversationUnreadSync'

const USER = 'user_test_1'

describe('conversationUnreadSync multi-tab', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('save/load round-trips under user-scoped key', () => {
    saveUnreadMap(USER, { a: true, b: true })
    expect(loadUnreadMap(USER)).toEqual({ a: true, b: true })
    expect(localStorage.getItem(unreadStorageKey(USER))).toContain('a')
    expect(localStorage.getItem(UNREAD_STORAGE_KEY_LEGACY)).toBeNull()
  })

  it('publishMark merges into storage without dropping other ids', () => {
    saveUnreadMap(USER, { c: true })
    const next = publishMark(USER, 'a')
    expect(next).toEqual({ c: true, a: true })
    expect(loadUnreadMap(USER)).toEqual({ c: true, a: true })
  })

  it('publishClear removes only the target id', () => {
    saveUnreadMap(USER, { a: true, c: true })
    expect(publishClear(USER, 'c')).toEqual({ a: true })
    expect(loadUnreadMap(USER)).toEqual({ a: true })
  })

  it('subscribeUnreadSync delivers BroadcastChannel mark/clear ops', () => {
    type Handler = (ev: MessageEvent) => void
    const listeners = new Map<string, Set<Handler>>()
    class FakeChannel {
      name: string
      private handler: Handler | null = null
      constructor(name: string) {
        this.name = name
        if (!listeners.has(name)) listeners.set(name, new Set())
      }
      addEventListener(type: string, handler: Handler): void {
        if (type !== 'message') return
        this.handler = handler
        listeners.get(this.name)?.add(handler)
      }
      removeEventListener(type: string, handler: Handler): void {
        if (type !== 'message') return
        listeners.get(this.name)?.delete(handler)
      }
      postMessage(data: unknown): void {
        for (const h of listeners.get(this.name) ?? []) {
          if (h !== this.handler) h({ data } as MessageEvent)
        }
      }
      close(): void {
        if (this.handler) listeners.get(this.name)?.delete(this.handler)
        this.handler = null
      }
    }
    const Original = globalThis.BroadcastChannel
    // @ts-expect-error test stub
    globalThis.BroadcastChannel = FakeChannel

    try {
      const received: Array<{ type: string; conversationId?: string }> = []
      const unsub = subscribeUnreadSync(USER, (ev) => {
        if (ev.type === 'mark' || ev.type === 'clear') {
          received.push({ type: ev.type, conversationId: ev.conversationId })
        }
      })

      publishMark(USER, 'peer1')
      publishClear(USER, 'peer1')

      expect(received).toEqual([
        { type: 'mark', conversationId: 'peer1' },
        { type: 'clear', conversationId: 'peer1' },
      ])
      unsub()
    } finally {
      globalThis.BroadcastChannel = Original
    }
  })

  it('subscribeUnreadSync reacts to storage events for the user key', () => {
    const received: Array<{ type: string }> = []
    const unsub = subscribeUnreadSync(USER, (ev) => {
      received.push({ type: ev.type })
    })

    window.dispatchEvent(
      new StorageEvent('storage', {
        key: unreadStorageKey(USER),
        newValue: JSON.stringify({ fromStorage: true }),
        storageArea: localStorage,
      }),
    )

    expect(received).toEqual([{ type: 'replace' }])
    unsub()
  })

  it('ignores storage events for other keys', () => {
    const received: Array<{ type: string }> = []
    const unsub = subscribeUnreadSync(USER, (ev) => {
      received.push({ type: ev.type })
    })
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'unrelated',
        newValue: '{"x":true}',
        storageArea: localStorage,
      }),
    )
    expect(received).toEqual([])
    unsub()
  })

  it('dropLegacyUnreadStorage removes the unscoped key', () => {
    localStorage.setItem(UNREAD_STORAGE_KEY_LEGACY, JSON.stringify({ old: true }))
    dropLegacyUnreadStorage()
    expect(localStorage.getItem(UNREAD_STORAGE_KEY_LEGACY)).toBeNull()
  })

  it('parseUnreadMap strips invalid shapes', () => {
    expect(parseUnreadMap('[]')).toEqual({})
    expect(parseUnreadMap('"x"')).toEqual({})
    expect(parseUnreadMap('{"a":true,"b":false}')).toEqual({ a: true })
  })
})
