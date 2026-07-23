/**
 * Cross-tab unread conversation sync.
 *
 * - Per-user localStorage (reload) + BroadcastChannel ops (live multi-tab).
 * - Mutations are per-conversation mark/clear so concurrent tabs do not
 *   clobber each other with last-writer-wins full-map snapshots.
 * - Not multi-device / not server-backed.
 */

export type UnreadConversationIds = Record<string, true>

/** Legacy unscoped key from the first implementation — removed on bind. */
export const UNREAD_STORAGE_KEY_LEGACY = 'cubeplex:conversation-unread-v1'

export const UNREAD_STORAGE_KEY_PREFIX = 'cubeplex:conversation-unread-v1:'
export const UNREAD_CHANNEL_PREFIX = 'cubeplex-conversation-unread:'

export type UnreadRemoteEvent =
  | { type: 'mark'; conversationId: string; at: number }
  | { type: 'clear'; conversationId: string; before: number }
  /** Full replace — only from storage events (best-effort fallback). */
  | { type: 'replace'; ids: UnreadConversationIds }

type UnreadChannelMessage =
  | { type: 'mark'; conversationId: string; at: number }
  | { type: 'clear'; conversationId: string; before: number }

function canUseStorage(): boolean {
  return typeof localStorage !== 'undefined'
}

function canUseBroadcastChannel(): boolean {
  return typeof BroadcastChannel !== 'undefined'
}

export function unreadStorageKey(userId: string): string {
  return `${UNREAD_STORAGE_KEY_PREFIX}${userId}`
}

export function unreadChannelName(userId: string): string {
  return `${UNREAD_CHANNEL_PREFIX}${userId}`
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

export function loadUnreadMap(userId: string): UnreadConversationIds {
  if (!canUseStorage() || !userId) return {}
  try {
    return parseUnreadMap(localStorage.getItem(unreadStorageKey(userId)))
  } catch {
    return {}
  }
}

export function saveUnreadMap(userId: string, ids: UnreadConversationIds): void {
  if (!canUseStorage() || !userId) return
  try {
    localStorage.setItem(unreadStorageKey(userId), JSON.stringify(ids))
  } catch {
    // Quota / private mode — in-memory still works for this tab.
  }
}

export function clearUnreadStorage(userId: string): void {
  if (!canUseStorage() || !userId) return
  try {
    localStorage.removeItem(unreadStorageKey(userId))
  } catch {
    // ignore
  }
}

/** Drop the pre-scoping global key so logout/login cannot revive shared state. */
export function dropLegacyUnreadStorage(): void {
  if (!canUseStorage()) return
  try {
    localStorage.removeItem(UNREAD_STORAGE_KEY_LEGACY)
  } catch {
    // ignore
  }
}

export function unreadMapsEqual(a: UnreadConversationIds, b: UnreadConversationIds): boolean {
  const aKeys = Object.keys(a)
  const bKeys = Object.keys(b)
  if (aKeys.length !== bKeys.length) return false
  return aKeys.every((k) => b[k] === true)
}

function broadcastOp(userId: string, msg: UnreadChannelMessage): void {
  if (!canUseBroadcastChannel() || !userId) return
  try {
    const channel = new BroadcastChannel(unreadChannelName(userId))
    channel.postMessage(msg)
    channel.close()
  } catch {
    // Ignore — storage remains a best-effort fallback.
  }
}

/**
 * Mark one conversation unread: RMW localStorage + broadcast mark op.
 * `at` is a monotonic-ish timestamp used so delayed clears cannot wipe a
 * newer mark. Returns the merged map after applying the mark to storage.
 *
 * Note: localStorage RMW is best-effort under concurrent tab writes; live
 * multi-tab correctness prefers BroadcastChannel ops. A true atomic merge
 * would need a versioned op log — out of scope for this UI badge.
 */
export function publishMark(
  userId: string,
  conversationId: string,
  at: number = Date.now(),
): UnreadConversationIds {
  const prev = loadUnreadMap(userId)
  if (prev[conversationId]) {
    broadcastOp(userId, { type: 'mark', conversationId, at })
    return prev
  }
  const next: UnreadConversationIds = { ...prev, [conversationId]: true }
  saveUnreadMap(userId, next)
  broadcastOp(userId, { type: 'mark', conversationId, at })
  return next
}

/**
 * Clear one conversation unread: RMW localStorage + broadcast clear op.
 * Peers only drop a mark if their local mark timestamp is `<= before`.
 */
export function publishClear(
  userId: string,
  conversationId: string,
  before: number = Date.now(),
): UnreadConversationIds {
  const prev = loadUnreadMap(userId)
  if (!prev[conversationId]) {
    // Still broadcast so peers that hold the id drop it (e.g. present-tab rejection).
    broadcastOp(userId, { type: 'clear', conversationId, before })
    return prev
  }
  const next: UnreadConversationIds = { ...prev }
  delete next[conversationId]
  saveUnreadMap(userId, next)
  broadcastOp(userId, { type: 'clear', conversationId, before })
  return next
}

/**
 * Subscribe to remote unread updates for one user.
 * BC delivers per-conversation ops; storage events deliver full maps.
 */
export function subscribeUnreadSync(
  userId: string,
  onRemote: (event: UnreadRemoteEvent) => void,
): () => void {
  if (typeof window === 'undefined' || !userId) return () => {}

  let channel: BroadcastChannel | null = null
  const onMessage = (ev: MessageEvent<UnreadChannelMessage>) => {
    const data = ev.data
    if (!data || typeof data !== 'object') return
    if (
      data.type === 'mark' &&
      typeof data.conversationId === 'string' &&
      typeof data.at === 'number'
    ) {
      onRemote({ type: 'mark', conversationId: data.conversationId, at: data.at })
      return
    }
    if (
      data.type === 'clear' &&
      typeof data.conversationId === 'string' &&
      typeof data.before === 'number'
    ) {
      onRemote({
        type: 'clear',
        conversationId: data.conversationId,
        before: data.before,
      })
    }
  }

  if (canUseBroadcastChannel()) {
    try {
      channel = new BroadcastChannel(unreadChannelName(userId))
      channel.addEventListener('message', onMessage)
    } catch {
      channel = null
    }
  }

  const storageKey = unreadStorageKey(userId)
  const onStorage = (ev: StorageEvent) => {
    if (ev.key !== storageKey) return
    onRemote({ type: 'replace', ids: parseUnreadMap(ev.newValue) })
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
