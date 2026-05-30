import type { CitationData } from '../types';
export interface CitationStore {
    /** conversationId → citationId → CitationData */
    citations: Record<string, Record<number, CitationData>>;
    addCitation: (conversationId: string, data: CitationData) => void;
    loadCitations: (conversationId: string, citations: CitationData[]) => void;
    getCitation: (conversationId: string, citationId: number) => CitationData | undefined;
    clearConversation: (conversationId: string) => void;
}
export declare const useCitationStore: import("zustand").UseBoundStore<import("zustand").StoreApi<CitationStore>>;
//# sourceMappingURL=citationStore.d.ts.map