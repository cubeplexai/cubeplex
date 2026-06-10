'use client'

import { create } from 'zustand'

interface ComposerDraftState {
  draft: string | null
  setDraft: (text: string) => void
  consume: () => string | null
}

/** Tiny module-level bridge so PromptCards (and similar) can fill the
 *  InputBar's local content without restructuring InputBar's streaming
 *  state machine. PromptCards setDraft → InputBar effect consumes once. */
export const useComposerDraft = create<ComposerDraftState>((set, get) => ({
  draft: null,
  setDraft: (text) => set({ draft: text }),
  consume: () => {
    const d = get().draft
    if (d !== null) set({ draft: null })
    return d
  },
}))
