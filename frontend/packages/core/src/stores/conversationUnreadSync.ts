/**
 * Cross-tab unread conversation sync.
 *
 * Persist the unread id set to localStorage (reload + multi-tab storage events)
 * and mirror mutations over BroadcastChannel so tabs that did not own the SSE
 * still show / clear the sidebar dot.
 *
 * Not multi-device / not server-backed — browser-local only.
 */

export type UnreadConversationIds = Record<string, true>

export const UNREAD_STORAGE_KEY = 'cubeplex:conversation-unread-v1'
export const UNREAD_CHANNEL_NAME = 'cubeplex-conversation-unread'

type UnreadChannelMessage = {
  type: 'sync'
  ids: UnreadConversationIds
}

function canUseStorage(): boolean {
  return typeof localStorage !== 'undefined'
}

function canUseBroadcastChannel(): boolean {
  return typeof BroadcastChannel !== 'undefined'
}

export function parseUnreadMap(raw: string | null): UnreadConversationIds {
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: UnreadConversationIds = {}
    for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (value === true && id.length > 0) out[id] = true
    }
    return out
  } catch {
    return {}
  }
}

export function loadUnreadMap(): UnreadConversationIds {
  if (!canUseStorage()) return {}
  try {
    return parseUnreadMap(localStorage.getItem(UNREAD_STORAGE_KEY))
  } catch {
    return {}
  }
}

export function saveUnreadMap(ids: UnreadConversationIds): void {
  if (!canUseStorage()) return
  try {
    localStorage.setItem(UNREAD_STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // Quota / private mode — in-memory still works for this tab.
  }
}

export function unreadMapsEqual(a: UnreadConversationIds, b: UnreadConversationIds): boolean {
  const aKeys = Object.keys(a)
  const bKeys = Object.keys(b)
  if (aKeys.length !== bKeys.length) return false
  return aKeys.every((k) => b[k] === true)
}

/** Broadcast the full map so peers can replace their set. */
export function broadcastUnreadMap(ids: UnreadConversationIds): void {
  if (!canUseBroadcastChannel()) return
  try {
    const channel = new BroadcastChannel(UNREAD_CHANNEL_NAME)
    const msg: UnreadChannelMessage = { type: 'sync', ids }
    channel.postMessage(msg)
    channel.close()
  } catch {
    // Ignore — storage event remains a fallback for other tabs.
  }
}

/**
 * Persist + notify other tabs. Call only from local mark/clear mutations
 * (not when applying a remote sync payload).
 */
export function publishUnreadMap(ids: UnreadConversationIds): void {
  saveUnreadMap(ids)
  broadcastUnreadMap(ids)
}

/**
 * Subscribe to remote unread updates (BroadcastChannel + storage events).
 * Returns an unsubscribe function. Safe no-op in non-browser environments.
 */
export function subscribeUnreadSync(onRemote: (ids: UnreadConversationIds) => void): () => void {
  if (typeof window === 'undefined') return () => {}

  let channel: BroadcastChannel | null = null
  const onMessage = (ev: MessageEvent<UnreadChannelMessage>) => {
    const data = ev.data
    if (!data || data.type !== 'sync' || !data.ids || typeof data.ids !== 'object') return
    onRemote(parseUnreadMap(JSON.stringify(data.ids)))
  }

  if (canUseBroadcastChannel()) {
    try {
      channel = new BroadcastChannel(UNREAD_CHANNEL_NAME)
      channel.addEventListener('message', onMessage)
    } catch {
      channel = null
    }
  }

  const onStorage = (ev: StorageEvent) => {
    if (ev.key !== UNREAD_STORAGE_KEY) return
    onRemote(parseUnreadMap(ev.newValue))
  }
  window.addEventListener('storage', onStorage)

  return () => {
    window.removeEventListener('storage', onStorage)
    if (channel) {
      channel.removeEventListener('message', onMessage)
      channel.close()
    }
  }
}
