import { create } from 'zustand'
import type { UserEvent } from '../types'

interface MemoryEventState {
  byConversation: Record<string, UserEvent[]>
  add: (ev: UserEvent) => void
  markRead: (id: string) => void
  reset: () => void
}

export const useMemoryEventStore = create<MemoryEventState>((set) => ({
  byConversation: {},
  add: (ev) =>
    set((s) => {
      const conv = ev.payload.conversation_id
      const existing = s.byConversation[conv] ?? []
      if (existing.some((e) => e.id === ev.id)) return s // dedupe
      return { byConversation: { ...s.byConversation, [conv]: [...existing, ev] } }
    }),
  markRead: (id) =>
    set((s) => {
      const next: Record<string, UserEvent[]> = {}
      for (const [k, list] of Object.entries(s.byConversation)) {
        next[k] = list.filter((e) => e.id !== id)
      }
      return { byConversation: next }
    }),
  // Called by useUserEvents when the authenticated user changes (logout +
  // re-login as a different user in the same tab). Prevents the previous
  // user's events from leaking into the new session's chip/toast UI.
  reset: () => set({ byConversation: {} }),
}))
