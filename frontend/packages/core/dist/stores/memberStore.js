import { create } from 'zustand';
import { listOrgMembers, addOrgMember as apiAddOrgMember, updateOrgMemberRole as apiUpdateOrgMemberRole, removeOrgMember as apiRemoveOrgMember, listWsMembers, listAvailableMembers as apiListAvailable, addWsMember as apiAddWsMember, updateWsMemberRole as apiUpdateWsMemberRole, removeWsMember as apiRemoveWsMember, } from '../api/members';
export const useMemberStore = create((set, get) => ({
    orgMembers: [],
    orgLoading: false,
    wsMembers: [],
    wsLoading: false,
    available: [],
    async loadOrgMembers(client) {
        set({ orgLoading: true });
        try {
            const orgMembers = await listOrgMembers(client);
            set({ orgMembers });
        }
        finally {
            set({ orgLoading: false });
        }
    },
    async addOrgMember(client, email, role) {
        await apiAddOrgMember(client, email, role);
        await get().loadOrgMembers(client);
    },
    async updateOrgMemberRole(client, userId, role) {
        await apiUpdateOrgMemberRole(client, userId, role);
        set((s) => ({
            orgMembers: s.orgMembers.map((m) => m.user_id === userId ? { ...m, role: role } : m),
        }));
    },
    async removeOrgMember(client, userId) {
        await apiRemoveOrgMember(client, userId);
        set((s) => ({ orgMembers: s.orgMembers.filter((m) => m.user_id !== userId) }));
    },
    async loadWsMembers(client, wsId) {
        set({ wsLoading: true });
        try {
            const wsMembers = await listWsMembers(client, wsId);
            set({ wsMembers });
        }
        finally {
            set({ wsLoading: false });
        }
    },
    async loadAvailable(client, wsId) {
        const available = await apiListAvailable(client, wsId);
        set({ available });
    },
    async addWsMember(client, wsId, userId, role) {
        await apiAddWsMember(client, wsId, userId, role);
        await get().loadWsMembers(client, wsId);
        await get().loadAvailable(client, wsId);
    },
    async updateWsMemberRole(client, wsId, userId, role) {
        await apiUpdateWsMemberRole(client, wsId, userId, role);
        set((s) => ({
            wsMembers: s.wsMembers.map((m) => m.user_id === userId ? { ...m, role: role } : m),
        }));
    },
    async removeWsMember(client, wsId, userId) {
        await apiRemoveWsMember(client, wsId, userId);
        set((s) => ({ wsMembers: s.wsMembers.filter((m) => m.user_id !== userId) }));
        await get().loadAvailable(client, wsId);
    },
    reset() {
        set({
            orgMembers: [],
            orgLoading: false,
            wsMembers: [],
            wsLoading: false,
            available: [],
        });
    },
}));
//# sourceMappingURL=memberStore.js.map