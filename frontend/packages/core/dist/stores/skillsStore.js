import { create } from 'zustand';
import { discoverSkills, installSkill, refreshSkill } from '../api/skills';
export const useSkillsStore = create((set) => ({
    candidates: [],
    query: '',
    installing: {},
    lastInstalled: null,
    async search(client, wsId, q) {
        set({ query: q });
        const candidates = await discoverSkills(client, wsId, q);
        set({ candidates });
    },
    async install(client, wsId, candidateId) {
        set((s) => ({ installing: { ...s.installing, [candidateId]: true } }));
        try {
            const r = await installSkill(client, wsId, candidateId);
            set((s) => ({
                lastInstalled: { canonical_name: r.canonical_name, version: r.installed_version },
                installing: { ...s.installing, [candidateId]: false },
            }));
        }
        catch (e) {
            set((s) => ({ installing: { ...s.installing, [candidateId]: false } }));
            throw e;
        }
    },
    async refresh(client, wsId, skillId) {
        const r = await refreshSkill(client, wsId, skillId);
        return r.changed;
    },
    reset: () => set({ candidates: [], query: '', installing: {}, lastInstalled: null }),
}));
//# sourceMappingURL=skillsStore.js.map