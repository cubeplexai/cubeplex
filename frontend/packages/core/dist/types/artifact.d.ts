export interface Artifact {
    id: string;
    conversation_id: string;
    name: string;
    artifact_type: 'file' | 'website' | 'code' | 'document' | 'image' | 'data' | 'skill';
    path: string;
    entry_file?: string | null;
    mime_type?: string | null;
    description?: string | null;
    created_at: string;
    updated_at: string;
    version: number;
}
export interface ArtifactVersion {
    id: string;
    artifact_id: string;
    version: number;
    name: string;
    description?: string | null;
    path: string;
    entry_file?: string | null;
    mime_type?: string | null;
    created_at: string;
}
//# sourceMappingURL=artifact.d.ts.map