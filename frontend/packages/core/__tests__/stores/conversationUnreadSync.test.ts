import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  UNREAD_CHANNEL_NAME,
  UNREAD_STORAGE_KEY,
  broadcastUnreadMap,
  loadUnreadMap,
  parseUnreadMap,
  publishUnreadMap,
  saveUnreadMap,
  subscribeUnreadSync,
} from '../../src/stores/conversationUnreadSync'

describe('conversationUnreadSync multi-tab', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('save/load round-trips', () => {
    saveUnreadMap({ a: true, b: true })
    expect(loadUnreadMap()).toEqual({ a: true, b: true })
  })

  it('subscribeUnreadSync delivers BroadcastChannel messages', () => {
    // jsdom's BroadcastChannel is incomplete; stub a minimal bus for this test.
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
      const received: Array<Record<string, true>> = []
      const unsub = subscribeUnreadSync((ids) => {
        received.push(ids)
      })

      const peer = new BroadcastChannel(UNREAD_CHANNEL_NAME)
      peer.postMessage({ type: 'sync', ids: { peer1: true } })
      peer.close()

      expect(received).toEqual([{ peer1: true }])
      unsub()
    } finally {
      globalThis.BroadcastChannel = Original
    }
  })

  it('subscribeUnreadSync reacts to storage events', () => {
    const received: Array<Record<string, true>> = []
    const unsub = subscribeUnreadSync((ids) => {
      received.push(ids)
    })

    const ev = new StorageEvent('storage', {
      key: UNREAD_STORAGE_KEY,
      newValue: JSON.stringify({ fromStorage: true }),
      storageArea: localStorage,
    })
    window.dispatchEvent(ev)

    expect(received).toEqual([{ fromStorage: true }])
    unsub()
  })

  it('ignores storage events for other keys', () => {
    const received: Array<Record<string, true>> = []
    const unsub = subscribeUnreadSync((ids) => {
      received.push(ids)
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

  it('broadcastUnreadMap is best-effort when channel construction fails', () => {
    const Original = globalThis.BroadcastChannel
    // @ts-expect-error test stub
    globalThis.BroadcastChannel = class {
      constructor() {
        throw new Error('no channel')
      }
    }
    expect(() => broadcastUnreadMap({ a: true })).not.toThrow()
    globalThis.BroadcastChannel = Original
  })

  it('parseUnreadMap strips invalid shapes', () => {
    expect(parseUnreadMap('[]')).toEqual({})
    expect(parseUnreadMap('"x"')).toEqual({})
  })

  it('publishUnreadMap persists for other tabs to load', () => {
    publishUnreadMap({ tabA: true })
    expect(localStorage.getItem(UNREAD_STORAGE_KEY)).toContain('tabA')
  })
})
