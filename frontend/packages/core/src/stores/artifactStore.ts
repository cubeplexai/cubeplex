// frontend/packages/core/src/stores/artifactStore.ts
import { create } from 'zustand'
import type { Artifact } from '../types'

export interface ArtifactStore {
  /** Artifacts indexed by conversationId, then by artifactId */
  artifacts: Record<string, Record<string, Artifact>>

  /** Currently previewed artifact */
  previewArtifactId: string | null
  previewConversationId: string | null

  /** Add or update an artifact for a conversation */
  addOrUpdate: (conversationId: string, artifact: Artifact) => void

  /** Get all artifacts for a conversation */
  getArtifacts: (conversationId: string) => Artifact[]

  /** Open artifact preview panel */
  openPreview: (conversationId: string, artifactId: string) => void

  /** Close artifact preview panel */
  closePreview: () => void

  /** Clear artifacts for a conversation */
  clearConversation: (conversationId: string) => void
}

export const useArtifactStore = create<ArtifactStore>((set, get) => ({
  artifacts: {},
  previewArtifactId: null,
  previewConversationId: null,

  addOrUpdate: (conversationId, artifact) =>
    set((state) => ({
      artifacts: {
        ...state.artifacts,
        [conversationId]: {
          ...state.artifacts[conversationId],
          [artifact.id]: artifact,
        },
      },
    })),

  getArtifacts: (conversationId) => {
    const conv = get().artifacts[conversationId]
    return conv ? Object.values(conv) : []
  },

  openPreview: (conversationId, artifactId) =>
    set({ previewArtifactId: artifactId, previewConversationId: conversationId }),

  closePreview: () =>
    set({ previewArtifactId: null, previewConversationId: null }),

  clearConversation: (conversationId) =>
    set((state) => {
      const { [conversationId]: _, ...rest } = state.artifacts
      return { artifacts: rest }
    }),
}))
