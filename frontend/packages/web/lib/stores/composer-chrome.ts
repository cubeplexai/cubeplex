/**
 * Cross-component seams for slash-command open-controls that live outside
 * InputBar (SharePanel in AppShell header, Sidebar rename).
 * InputBar requests; the owning component reacts and **consumes** the request
 * so stale nonces never replay on remount.
 */
import { create } from 'zustand'

export type ChromeRequest = { conversationId: string; nonce: number }

type ComposerChromeState = {
  shareRequest: ChromeRequest | null
  requestOpenShare: (conversationId: string) => void
  /** Clear only if the nonce still matches (consumable event). */
  consumeShareRequest: (nonce: number) => void

  renameRequest: ChromeRequest | null
  requestRename: (conversationId: string) => void
  consumeRenameRequest: (nonce: number) => void
}

let nonce = 0

export const useComposerChromeStore = create<ComposerChromeState>((set) => ({
  shareRequest: null,
  requestOpenShare: (conversationId) => {
    nonce += 1
    set({ shareRequest: { conversationId, nonce } })
  },
  consumeShareRequest: (n) => {
    set((s) => (s.shareRequest?.nonce === n ? { shareRequest: null } : s))
  },
  renameRequest: null,
  requestRename: (conversationId) => {
    nonce += 1
    set({ renameRequest: { conversationId, nonce } })
  },
  consumeRenameRequest: (n) => {
    set((s) => (s.renameRequest?.nonce === n ? { renameRequest: null } : s))
  },
}))
