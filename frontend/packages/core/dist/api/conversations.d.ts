import type { Artifact, ArtifactVersion, Conversation, Message } from '../types';
import { type ApiClient } from './client';
export declare function createConversation(client: ApiClient, title?: string, opts?: {
    draft?: boolean;
}): Promise<Conversation>;
export declare function listConversations(client: ApiClient, limit?: number, offset?: number): Promise<Conversation[]>;
export declare function getConversation(client: ApiClient, id: string): Promise<Conversation>;
export declare function deleteConversation(client: ApiClient, id: string): Promise<void>;
export declare function renameConversation(client: ApiClient, id: string, title: string): Promise<Conversation>;
export declare function setPinConversation(client: ApiClient, id: string, isPinned: boolean): Promise<Conversation>;
export declare function generateConversationTitle(client: ApiClient, id: string, content: string): Promise<Conversation>;
export declare function listMessages(client: ApiClient, conversationId: string, limit?: number, offset?: number): Promise<Message[]>;
export declare function listArtifacts(client: ApiClient, conversationId: string): Promise<Artifact[]>;
export declare function listArtifactVersions(client: ApiClient, conversationId: string, artifactId: string): Promise<ArtifactVersion[]>;
export interface PreviewTokenResponse {
    download_url: string;
    viewer_url: string;
}
export declare function requestPreviewToken(client: ApiClient, conversationId: string, artifactId: string, version?: number): Promise<PreviewTokenResponse>;
//# sourceMappingURL=conversations.d.ts.map