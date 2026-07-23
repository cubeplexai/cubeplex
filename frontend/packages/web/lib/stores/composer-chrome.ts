/**
 * Cross-component seams for slash-command open-controls that live outside
 * InputBar (SharePanel in AppShell header, Sidebar rename).
 * InputBar requests; the owning component reacts.
 */
import { create } from 'zustand'

type ComposerChromeState = {
  /** Bumped to request SharePanel open for conversationId. */
  shareRequest: { conversationId: string; nonce: number } | null
  requestOpenShare: (conversationId: string) => void

  /** Bumped to enter rename on the matching sidebar row. */
  renameRequest: { conversationId: string; nonce: number } | null
  requestRename: (conversationId: string) => void
}

let nonce = 0

export const useComposerChromeStore = create<ComposerChromeState>((set) => ({
  shareRequest: null,
  requestOpenShare: (conversationId) => {
    nonce += 1
    set({ shareRequest: { conversationId, nonce } })
  },
  renameRequest: null,
  requestRename: (conversationId) => {
    nonce += 1
    set({ renameRequest: { conversationId, nonce } })
  },
}))
