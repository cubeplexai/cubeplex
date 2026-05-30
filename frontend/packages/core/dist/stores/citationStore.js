import { create } from 'zustand';
export const useCitationStore = create((set, get) => ({
    citations: {},
    addCitation(conversationId, data) {
        set((s) => ({
            citations: {
                ...s.citations,
                [conversationId]: {
                    ...s.citations[conversationId],
                    [data.citation_id]: data,
                },
            },
        }));
    },
    loadCitations(conversationId, citations) {
        const map = {};
        for (const c of citations) {
            map[c.citation_id] = c;
        }
        set((s) => ({
            citations: {
                ...s.citations,
                [conversationId]: {
                    ...s.citations[conversationId],
                    ...map,
                },
            },
        }));
    },
    getCitation(conversationId, citationId) {
        return get().citations[conversationId]?.[citationId];
    },
    clearConversation(conversationId) {
        set((s) => {
            const { [conversationId]: _, ...rest } = s.citations;
            return { citations: rest };
        });
    },
}));
//# sourceMappingURL=citationStore.js.map