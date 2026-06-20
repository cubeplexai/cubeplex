// frontend/packages/core/src/stores/artifactStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { listArtifacts, listArtifactVersions } from '../api/conversations'
import type { Artifact, ArtifactVersion } from '../types'

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

  /** Remove a single artifact from a conversation's map */
  removeArtifact: (conversationId: string, artifactId: string) => void

  /** Cached version lists per artifactId */
  versions: Record<string, ArtifactVersion[]>

  /** Selected version per artifactId (null = latest) */
  selectedVersion: Record<string, number | null>

  /** Load versions for an artifact */
  loadVersions: (client: ApiClient, conversationId: string, artifactId: string) => Promise<void>

  /** Select a specific version for an artifact */
  selectVersion: (artifactId: string, version: number | null) => void

  /** Get the selected version for an artifact */
  getSelectedVersion: (artifactId: string) => number | null
}

export const useArtifactStore = create<ArtifactStore>((set, get) => ({
  artifacts: {},
  loading: {},
  versions: {},
  selectedVersion: {},

  addOrUpdate: (conversationId, artifact) =>
    set((state) => {
      const prev = state.artifacts[conversationId]?.[artifact.id]
      const versionChanged = prev && prev.version !== artifact.version
      return {
        artifacts: {
          ...state.artifacts,
          [conversationId]: {
            ...state.artifacts[conversationId],
            [artifact.id]: artifact,
          },
        },
        // Invalidate cached version list when version bumps
        ...(versionChanged
          ? { versions: { ...state.versions, [artifact.id]: undefined as never } }
          : {}),
      }
    }),

  async loadArtifacts(client, conversationId) {
    if (get().loading[conversationId]) return
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

  removeArtifact: (conversationId, artifactId) =>
    set((state) => {
      const conv = state.artifacts[conversationId]
      if (!conv || !(artifactId in conv)) return state
      const { [artifactId]: _, ...restConv } = conv
      return { artifacts: { ...state.artifacts, [conversationId]: restConv } }
    }),

  async loadVersions(client, conversationId, artifactId) {
    if (get().versions[artifactId]) return
    try {
      const versions = await listArtifactVersions(client, conversationId, artifactId)
      set((state) => ({
        versions: { ...state.versions, [artifactId]: versions },
      }))
    } catch {
      // Versions are non-critical; silently ignore load failures
    }
  },

  selectVersion: (artifactId, version) =>
    set((state) => ({
      selectedVersion: { ...state.selectedVersion, [artifactId]: version },
    })),

  getSelectedVersion: (artifactId) => get().selectedVersion[artifactId] ?? null,
}))
