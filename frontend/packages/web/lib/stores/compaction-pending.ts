/**
 * Page-local "compact is in flight" flag. The finished marker is the only
 * row written to history; this flag is not bootstrapped and does not survive
 * a refresh.
 */
import { create } from 'zustand'

/** Wait before the waiting line so a fast compact does not flash. */
export const COMPACT_PENDING_DELAY_MS = 500

type CompactionPendingState = {
  compactingConversationIds: Record<string, true>
  beginCompacting: (conversationId: string) => void
  endCompacting: (conversationId: string) => void
}

export const useCompactionPendingStore = create<CompactionPendingState>((set) => ({
  compactingConversationIds: {},
  beginCompacting: (conversationId) => {
    set((state) => {
      if (state.compactingConversationIds[conversationId]) return state
      return {
        compactingConversationIds: {
          ...state.compactingConversationIds,
          [conversationId]: true,
        },
      }
    })
  },
  endCompacting: (conversationId) => {
    set((state) => {
      if (!state.compactingConversationIds[conversationId]) return state
      const next = { ...state.compactingConversationIds }
      delete next[conversationId]
      return { compactingConversationIds: next }
    })
  },
}))
