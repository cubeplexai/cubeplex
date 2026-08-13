'use client'

import { create } from 'zustand'

interface PendingDraft {
  text: string
  conversationId: string | null
  placement: 'replace' | 'prepend'
  /** monotonic counter so identical strings still trigger consumer effect */
  nonce: number
}

interface ComposerDraftState {
  pending: PendingDraft | null
  pendingByConversation: Record<string, PendingDraft>
  setDraft: (
    text: string,
    conversationId?: string | null,
    placement?: PendingDraft['placement'],
  ) => void
  consume: (conversationId?: string | null) => string | null
}

// Module-level monotonic counter — survives consume() that clears `pending`,
// so two identical setDraft calls always produce strictly increasing nonces.
let nextNonce = 1

/** Tiny module-level bridge so PromptCards (and similar) can fill the
 *  InputBar's local content without restructuring InputBar's streaming
 *  state machine. PromptCards setDraft → InputBar effect consumes once.
 *  Uses a {text, nonce} tuple so re-clicking the same card still re-fires. */
export const useComposerDraft = create<ComposerDraftState>((set, get) => ({
  pending: null,
  pendingByConversation: {},
  setDraft: (text, conversationId = null, placement = 'replace') =>
    set((state) => {
      const draft = { text, conversationId, placement, nonce: nextNonce++ }
      return conversationId === null
        ? { pending: draft }
        : {
            pendingByConversation: {
              ...state.pendingByConversation,
              [conversationId]: draft,
            },
          }
    }),
  consume: (conversationId = null) => {
    const state = get()
    const scoped = conversationId === null ? null : state.pendingByConversation[conversationId]
    const p = scoped ?? state.pending
    if (p === null) return null
    if (scoped) {
      set((current) => {
        const pendingByConversation = { ...current.pendingByConversation }
        delete pendingByConversation[conversationId!]
        return { pendingByConversation }
      })
    } else {
      set({ pending: null })
    }
    return p.text
  },
}))
