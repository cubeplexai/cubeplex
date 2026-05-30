import { create } from 'zustand';
import { fetchProviders, createProvider, updateProvider, deleteProvider } from '../api/providers';
export const useProvidersStore = create((set, _get) => ({
    providers: [],
    selectedId: null,
    loading: false,
    error: null,
    fetchProviders: async (client) => {
        set({ loading: true, error: null });
        try {
            const providers = await fetchProviders(client);
            set({ providers, loading: false });
        }
        catch (e) {
            set({ error: e.message, loading: false });
        }
    },
    selectProvider: (id) => set({ selectedId: id }),
    createProvider: async (client, body) => {
        const provider = await createProvider(client, body);
        set((s) => ({ providers: [...s.providers, provider] }));
        return provider;
    },
    updateProvider: async (client, id, body) => {
        const updated = await updateProvider(client, id, body);
        set((s) => ({
            providers: s.providers.map((p) => (p.id === id ? updated : p)),
        }));
    },
    deleteProvider: async (client, id) => {
        await deleteProvider(client, id);
        set((s) => ({
            providers: s.providers.filter((p) => p.id !== id),
            selectedId: s.selectedId === id ? null : s.selectedId,
        }));
    },
}));
//# sourceMappingURL=providersStore.js.map