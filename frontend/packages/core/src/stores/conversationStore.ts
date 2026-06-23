import { create } from 'zustand'
import type { Conversation, ConversationParticipant } from '../types'
import type { ApiClient } from '../api'
import {
  createConversation,
  listConversations,
  deleteConversation,
  renameConversation,
  setPinConversation,
  generateConversationTitle,
  forkConversation,
  inviteToGroup,
  listConversationParticipants,
} from '../api'

/** Pinned first, then recency desc — same invariant the backend uses. */
function sortPinnedFirst(list: Conversation[]): Conversation[] {
  return [...list].sort((a, b) => {
    if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  })
}

export interface ConversationStore {
  conversations: Conversation[]
  activeId: string | null
  isLoading: boolean
  isFetchingList: boolean
  error: string | null
  /** ids currently mid-pin-request — UI uses this to disable the button. */
  pinPending: Record<string, true>
  conversationParticipants: Record<string, ConversationParticipant[]>
  fetchList(client: ApiClient): Promise<void>
  create(client: ApiClient, title?: string, opts?: { draft?: boolean }): Promise<Conversation>
  fork(client: ApiClient, sourceId: string, afterRunId: string): Promise<Conversation>
  remove(client: ApiClient, id: string): Promise<void>
  rename(client: ApiClient, id: string, title: string): Promise<void>
  setPin(client: ApiClient, id: string, isPinned: boolean): Promise<void>
  generateTitle(client: ApiClient, id: string, content: string): Promise<void>
  setActive(id: string | null): void
  inviteToGroup(client: ApiClient, conversationId: string, userIds: string[]): Promise<void>
  fetchConversationParticipants(client: ApiClient, conversationId: string): Promise<void>
}

export const useConversationStore = create<ConversationStore>((set, get) => ({
  conversations: [],
  activeId: null,
  isLoading: false,
  isFetchingList: false,
  error: null,
  pinPending: {},
  conversationParticipants: {},

  async fetchList(client: ApiClient) {
    if (get().isFetchingList) return
    set({ isFetchingList: true, error: null })
    try {
      const conversations = await listConversations(client)
      set({ conversations })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isFetchingList: false })
    }
  },

  async create(client: ApiClient, title?: string, opts?: { draft?: boolean }) {
    set({ isLoading: true, error: null })
    try {
      const convo = await createConversation(client, title, opts)
      set((s) => ({ conversations: sortPinnedFirst([convo, ...s.conversations]) }))
      return convo
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    } finally {
      set({ isLoading: false })
    }
  },

  async fork(client: ApiClient, sourceId: string, afterRunId: string) {
    // Mirrors `create`: hit the API, prepend the new conv into local
    // state, return it. Without the prepend the sidebar / header /
    // upgrade-to-topic controls render stale until the next list
    // fetch — the user lands on a conv that exists on the server but
    // not in the store. Errors propagate (MessageActions toasts them).
    const convo = await forkConversation(client, sourceId, afterRunId)
    set((s) => ({ conversations: sortPinnedFirst([convo, ...s.conversations]) }))
    return convo
  },

  async remove(client: ApiClient, id: string) {
    try {
      await deleteConversation(client, id)
      set((s) => ({
        conversations: s.conversations.filter((c) => c.id !== id),
        activeId: s.activeId === id ? null : s.activeId,
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  async rename(client: ApiClient, id: string, title: string) {
    try {
      const updated = await renameConversation(client, id, title)
      set((s) => ({
        conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  async setPin(client: ApiClient, id: string, isPinned: boolean) {
    // Drop the call if one is already in-flight for this id, so rapid
    // double-clicks can't race the server.
    if (get().pinPending[id]) return
    set((s) => ({ pinPending: { ...s.pinPending, [id]: true as const } }))
    try {
      const updated = await setPinConversation(client, id, isPinned)
      set((s) => ({
        conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    } finally {
      set((s) => {
        const next = { ...s.pinPending }
        delete next[id]
        return { pinPending: next }
      })
    }
  },

  async generateTitle(client: ApiClient, id: string, content: string) {
    try {
      const updated = await generateConversationTitle(client, id, content)
      set((s) => ({
        conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
      }))
    } catch {
      // Auto-title is best-effort; swallow errors
    }
  },

  setActive(id: string | null) {
    set({ activeId: id })
  },

  async inviteToGroup(client: ApiClient, conversationId: string, userIds: string[]) {
    try {
      const { participants, conversation } = await inviteToGroup(client, conversationId, userIds)
      set((s) => ({
        conversationParticipants: {
          ...s.conversationParticipants,
          [conversationId]: participants,
        },
        conversations: sortPinnedFirst(
          s.conversations.map((c) => (c.id === conversationId ? conversation : c)),
        ),
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  async fetchConversationParticipants(client: ApiClient, conversationId: string) {
    try {
      const { items } = await listConversationParticipants(client, conversationId)
      set((s) => ({
        conversationParticipants: {
          ...s.conversationParticipants,
          [conversationId]: items,
        },
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },
}))
