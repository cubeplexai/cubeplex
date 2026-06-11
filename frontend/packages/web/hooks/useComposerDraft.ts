'use client'

import { create } from 'zustand'

interface PendingDraft {
  text: string
  /** monotonic counter so identical strings still trigger consumer effect */
  nonce: number
}

interface ComposerDraftState {
  pending: PendingDraft | null
  setDraft: (text: string) => void
  consume: () => string | null
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
  setDraft: (text) => set({ pending: { text, nonce: nextNonce++ } }),
  consume: () => {
    const p = get().pending
    if (p === null) return null
    set({ pending: null })
    return p.text
  },
}))
