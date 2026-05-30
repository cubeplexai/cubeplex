import { create } from 'zustand';
import { fetchProvider, createModel, updateModel, deleteModel } from '../api/providers';
export const useModelsStore = create((set) => ({
    models: [],
    providerId: null,
    loading: false,
    error: null,
    fetchModels: async (client, providerId) => {
        set({ loading: true, error: null, models: [], providerId });
        try {
            const provider = await fetchProvider(client, providerId);
            set((s) => s.providerId === providerId ? { models: provider.models || [], loading: false } : s);
        }
        catch (e) {
            set((s) => s.providerId === providerId ? { error: e.message, loading: false } : s);
        }
    },
    clearModels: () => set({ models: [], providerId: null, loading: false, error: null }),
    createModel: async (client, providerId, body) => {
        const model = await createModel(client, providerId, body);
        set((s) => ({ models: [...s.models, model] }));
        return model;
    },
    updateModel: async (client, providerId, modelId, body) => {
        const updated = await updateModel(client, providerId, modelId, body);
        set((s) => ({
            models: s.models.map((m) => (m.id === modelId ? updated : m)),
        }));
    },
    deleteModel: async (client, providerId, modelId) => {
        await deleteModel(client, providerId, modelId);
        set((s) => ({ models: s.models.filter((m) => m.id !== modelId) }));
    },
}));
//# sourceMappingURL=modelsStore.js.map