import { create } from 'zustand';
import { fetchOrgLLMSettings, updateOrgLLMSettings } from '../api/providers';
export const useOrgModelSettingsStore = create((set) => ({
    settings: null,
    loading: false,
    error: null,
    fetchSettings: async (client) => {
        set({ loading: true, error: null });
        try {
            const settings = await fetchOrgLLMSettings(client);
            set({ settings, loading: false });
        }
        catch (e) {
            set({ error: e.message, loading: false });
        }
    },
    updateSettings: async (client, body) => {
        const settings = await updateOrgLLMSettings(client, body);
        set({ settings });
    },
}));
//# sourceMappingURL=orgModelSettingsStore.js.map