import { create } from 'zustand'
import type { Conversation } from '../types'
import type { ApiClient } from '../api'
import {
  createConversation,
  listConversations,
  deleteConversation,
  renameConversation,
} from '../api'

export interface ConversationStore {
  conversations: Conversation[]
  activeId: string | null
  isLoading: boolean
  isFetchingList: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  create(client: ApiClient, title?: string, opts?: { draft?: boolean }): Promise<Conversation>
  remove(client: ApiClient, id: string): Promise<void>
  rename(client: ApiClient, id: string, title: string): Promise<void>
  setActive(id: string | null): void
}

export const useConversationStore = create<ConversationStore>((set, get) => ({
  conversations: [],
  activeId: null,
  isLoading: false,
  isFetchingList: false,
  error: null,

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
      set((s) => ({ conversations: [convo, ...s.conversations] }))
      return convo
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    } finally {
      set({ isLoading: false })
    }
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
        conversations: s.conversations.map((c) => (c.id === id ? updated : c)),
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  setActive(id: string | null) {
    set({ activeId: id })
  },
}))
