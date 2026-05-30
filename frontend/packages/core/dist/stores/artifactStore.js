// frontend/packages/core/src/stores/artifactStore.ts
import { create } from 'zustand';
import { listArtifacts, listArtifactVersions } from '../api/conversations';
export const useArtifactStore = create((set, get) => ({
    artifacts: {},
    loading: {},
    versions: {},
    selectedVersion: {},
    addOrUpdate: (conversationId, artifact) => set((state) => {
        const prev = state.artifacts[conversationId]?.[artifact.id];
        const versionChanged = prev && prev.version !== artifact.version;
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
                ? { versions: { ...state.versions, [artifact.id]: undefined } }
                : {}),
        };
    }),
    async loadArtifacts(client, conversationId) {
        if (get().loading[conversationId])
            return;
        set((s) => ({ loading: { ...s.loading, [conversationId]: true } }));
        try {
            const artifacts = await listArtifacts(client, conversationId);
            if (artifacts.length === 0)
                return;
            const map = {};
            for (const a of artifacts) {
                map[a.id] = a;
            }
            set((state) => ({
                artifacts: {
                    ...state.artifacts,
                    [conversationId]: { ...state.artifacts[conversationId], ...map },
                },
            }));
        }
        catch {
            // Artifacts are non-critical; silently ignore load failures
        }
        finally {
            set((s) => ({ loading: { ...s.loading, [conversationId]: false } }));
        }
    },
    isLoading: (conversationId) => !!get().loading[conversationId],
    getArtifacts: (conversationId) => {
        const conv = get().artifacts[conversationId];
        return conv ? Object.values(conv) : [];
    },
    clearConversation: (conversationId) => set((state) => {
        const { [conversationId]: _, ...rest } = state.artifacts;
        return { artifacts: rest };
    }),
    async loadVersions(client, conversationId, artifactId) {
        if (get().versions[artifactId])
            return;
        try {
            const versions = await listArtifactVersions(client, conversationId, artifactId);
            set((state) => ({
                versions: { ...state.versions, [artifactId]: versions },
            }));
        }
        catch {
            // Versions are non-critical; silently ignore load failures
        }
    },
    selectVersion: (artifactId, version) => set((state) => ({
        selectedVersion: { ...state.selectedVersion, [artifactId]: version },
    })),
    getSelectedVersion: (artifactId) => get().selectedVersion[artifactId] ?? null,
}));
//# sourceMappingURL=artifactStore.js.map