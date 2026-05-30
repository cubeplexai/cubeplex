import type { ApiClient } from '../api/client';
import type { Artifact, ArtifactVersion } from '../types';
export interface ArtifactStore {
    /** Artifacts indexed by conversationId, then by artifactId */
    artifacts: Record<string, Record<string, Artifact>>;
    /** Loading state per conversation */
    loading: Record<string, boolean>;
    /** Add or update an artifact for a conversation */
    addOrUpdate: (conversationId: string, artifact: Artifact) => void;
    /** Load all artifacts for a conversation from the API */
    loadArtifacts: (client: ApiClient, conversationId: string) => Promise<void>;
    /** Check if artifacts are loading for a conversation */
    isLoading: (conversationId: string) => boolean;
    /** Get all artifacts for a conversation */
    getArtifacts: (conversationId: string) => Artifact[];
    /** Clear artifacts for a conversation */
    clearConversation: (conversationId: string) => void;
    /** Cached version lists per artifactId */
    versions: Record<string, ArtifactVersion[]>;
    /** Selected version per artifactId (null = latest) */
    selectedVersion: Record<string, number | null>;
    /** Load versions for an artifact */
    loadVersions: (client: ApiClient, conversationId: string, artifactId: string) => Promise<void>;
    /** Select a specific version for an artifact */
    selectVersion: (artifactId: string, version: number | null) => void;
    /** Get the selected version for an artifact */
    getSelectedVersion: (artifactId: string) => number | null;
}
export declare const useArtifactStore: import("zustand").UseBoundStore<import("zustand").StoreApi<ArtifactStore>>;
//# sourceMappingURL=artifactStore.d.ts.map