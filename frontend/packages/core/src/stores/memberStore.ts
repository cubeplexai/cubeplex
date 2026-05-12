import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  listOrgMembers,
  addOrgMember as apiAddOrgMember,
  updateOrgMemberRole as apiUpdateOrgMemberRole,
  removeOrgMember as apiRemoveOrgMember,
  listWsMembers,
  listAvailableMembers as apiListAvailable,
  addWsMember as apiAddWsMember,
  updateWsMemberRole as apiUpdateWsMemberRole,
  removeWsMember as apiRemoveWsMember,
  type OrgMember,
  type WsMember,
  type AvailableMember,
} from '../api/members'

export interface MemberStore {
  orgMembers: OrgMember[]
  orgLoading: boolean
  wsMembers: WsMember[]
  wsLoading: boolean
  available: AvailableMember[]

  loadOrgMembers(client: ApiClient): Promise<void>
  addOrgMember(client: ApiClient, email: string, role: string): Promise<void>
  updateOrgMemberRole(client: ApiClient, userId: string, role: string): Promise<void>
  removeOrgMember(client: ApiClient, userId: string): Promise<void>

  loadWsMembers(client: ApiClient, wsId: string): Promise<void>
  loadAvailable(client: ApiClient, wsId: string): Promise<void>
  addWsMember(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>
  updateWsMemberRole(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>
  removeWsMember(client: ApiClient, wsId: string, userId: string): Promise<void>

  reset(): void
}

export const useMemberStore = create<MemberStore>((set, get) => ({
  orgMembers: [],
  orgLoading: false,
  wsMembers: [],
  wsLoading: false,
  available: [],

  async loadOrgMembers(client) {
    set({ orgLoading: true })
    try {
      const orgMembers = await listOrgMembers(client)
      set({ orgMembers })
    } finally {
      set({ orgLoading: false })
    }
  },

  async addOrgMember(client, email, role) {
    await apiAddOrgMember(client, email, role)
    await get().loadOrgMembers(client)
  },

  async updateOrgMemberRole(client, userId, role) {
    await apiUpdateOrgMemberRole(client, userId, role)
    set((s) => ({
      orgMembers: s.orgMembers.map((m) =>
        m.user_id === userId ? { ...m, role: role as OrgMember['role'] } : m,
      ),
    }))
  },

  async removeOrgMember(client, userId) {
    await apiRemoveOrgMember(client, userId)
    set((s) => ({ orgMembers: s.orgMembers.filter((m) => m.user_id !== userId) }))
  },

  async loadWsMembers(client, wsId) {
    set({ wsLoading: true })
    try {
      const wsMembers = await listWsMembers(client, wsId)
      set({ wsMembers })
    } finally {
      set({ wsLoading: false })
    }
  },

  async loadAvailable(client, wsId) {
    const available = await apiListAvailable(client, wsId)
    set({ available })
  },

  async addWsMember(client, wsId, userId, role) {
    await apiAddWsMember(client, wsId, userId, role)
    await get().loadWsMembers(client, wsId)
    await get().loadAvailable(client, wsId)
  },

  async updateWsMemberRole(client, wsId, userId, role) {
    await apiUpdateWsMemberRole(client, wsId, userId, role)
    set((s) => ({
      wsMembers: s.wsMembers.map((m) =>
        m.user_id === userId ? { ...m, role: role as WsMember['role'] } : m,
      ),
    }))
  },

  async removeWsMember(client, wsId, userId) {
    await apiRemoveWsMember(client, wsId, userId)
    set((s) => ({ wsMembers: s.wsMembers.filter((m) => m.user_id !== userId) }))
    await get().loadAvailable(client, wsId)
  },

  reset() {
    set({
      orgMembers: [],
      orgLoading: false,
      wsMembers: [],
      wsLoading: false,
      available: [],
    })
  },
}))
