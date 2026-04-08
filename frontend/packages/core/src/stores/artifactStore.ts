// frontend/packages/core/src/stores/artifactStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { listArtifacts } from '../api/conversations'
import type { Artifact } from '../types'

export interface ArtifactStore {
  /** Artifacts indexed by conversationId, then by artifactId */
  artifacts: Record<string, Record<string, Artifact>>

  /** Loading state per conversation */
  loading: Record<string, boolean>

  /** Add or update an artifact for a conversation */
  addOrUpdate: (conversationId: string, artifact: Artifact) => void

  /** Load all artifacts for a conversation from the API */
  loadArtifacts: (client: ApiClient, conversationId: string) => Promise<void>

  /** Check if artifacts are loading for a conversation */
  isLoading: (conversationId: string) => boolean

  /** Get all artifacts for a conversation */
  getArtifacts: (conversationId: string) => Artifact[]

  /** Clear artifacts for a conversation */
  clearConversation: (conversationId: string) => void
}

export const useArtifactStore = create<ArtifactStore>((set, get) => ({
  artifacts: {},
  loading: {},

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

  async loadArtifacts(client, conversationId) {
    set((s) => ({ loading: { ...s.loading, [conversationId]: true } }))
    try {
      const artifacts = await listArtifacts(client, conversationId)
      if (artifacts.length === 0) return
      const map: Record<string, Artifact> = {}
      for (const a of artifacts) {
        map[a.id] = a
      }
      set((state) => ({
        artifacts: {
          ...state.artifacts,
          [conversationId]: { ...state.artifacts[conversationId], ...map },
        },
      }))
    } catch {
      // Artifacts are non-critical; silently ignore load failures
    } finally {
      set((s) => ({ loading: { ...s.loading, [conversationId]: false } }))
    }
  },

  isLoading: (conversationId) => !!get().loading[conversationId],

  getArtifacts: (conversationId) => {
    const conv = get().artifacts[conversationId]
    return conv ? Object.values(conv) : []
  },

  clearConversation: (conversationId) =>
    set((state) => {
      const { [conversationId]: _, ...rest } = state.artifacts
      return { artifacts: rest }
    }),
}))
