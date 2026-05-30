import { create } from 'zustand';
import { createConversation, listConversations, deleteConversation, renameConversation, setPinConversation, generateConversationTitle, } from '../api';
/** Pinned first, then recency desc — same invariant the backend uses. */
function sortPinnedFirst(list) {
    return [...list].sort((a, b) => {
        if (a.is_pinned !== b.is_pinned)
            return a.is_pinned ? -1 : 1;
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
}
export const useConversationStore = create((set, get) => ({
    conversations: [],
    activeId: null,
    isLoading: false,
    isFetchingList: false,
    error: null,
    pinPending: {},
    async fetchList(client) {
        if (get().isFetchingList)
            return;
        set({ isFetchingList: true, error: null });
        try {
            const conversations = await listConversations(client);
            set({ conversations });
        }
        catch (err) {
            set({ error: err.message });
        }
        finally {
            set({ isFetchingList: false });
        }
    },
    async create(client, title, opts) {
        set({ isLoading: true, error: null });
        try {
            const convo = await createConversation(client, title, opts);
            set((s) => ({ conversations: sortPinnedFirst([convo, ...s.conversations]) }));
            return convo;
        }
        catch (err) {
            set({ error: err.message });
            throw err;
        }
        finally {
            set({ isLoading: false });
        }
    },
    async remove(client, id) {
        try {
            await deleteConversation(client, id);
            set((s) => ({
                conversations: s.conversations.filter((c) => c.id !== id),
                activeId: s.activeId === id ? null : s.activeId,
            }));
        }
        catch (err) {
            set({ error: err.message });
            throw err;
        }
    },
    async rename(client, id, title) {
        try {
            const updated = await renameConversation(client, id, title);
            set((s) => ({
                conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
            }));
        }
        catch (err) {
            set({ error: err.message });
            throw err;
        }
    },
    async setPin(client, id, isPinned) {
        // Drop the call if one is already in-flight for this id, so rapid
        // double-clicks can't race the server.
        if (get().pinPending[id])
            return;
        set((s) => ({ pinPending: { ...s.pinPending, [id]: true } }));
        try {
            const updated = await setPinConversation(client, id, isPinned);
            set((s) => ({
                conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
            }));
        }
        catch (err) {
            set({ error: err.message });
            throw err;
        }
        finally {
            set((s) => {
                const next = { ...s.pinPending };
                delete next[id];
                return { pinPending: next };
            });
        }
    },
    async generateTitle(client, id, content) {
        try {
            const updated = await generateConversationTitle(client, id, content);
            set((s) => ({
                conversations: sortPinnedFirst(s.conversations.map((c) => (c.id === id ? updated : c))),
            }));
        }
        catch {
            // Auto-title is best-effort; swallow errors
        }
    },
    setActive(id) {
        set({ activeId: id });
    },
}));
//# sourceMappingURL=conversationStore.js.map