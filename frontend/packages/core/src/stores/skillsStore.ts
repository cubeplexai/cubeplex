import { create } from 'zustand'

import type { ApiClient } from '../api/client'
import { discoverSkills, installSkill, refreshSkill, type SkillCandidateOut } from '../api/skills'

export interface SkillsState {
  candidates: SkillCandidateOut[]
  query: string
  installing: Record<string, boolean>
  lastInstalled: { canonical_name: string; version: string } | null
  search: (client: ApiClient, wsId: string, q: string) => Promise<void>
  install: (client: ApiClient, wsId: string, candidateId: string) => Promise<void>
  refresh: (client: ApiClient, wsId: string, skillId: string) => Promise<boolean>
  reset: () => void
}

export const useSkillsStore = create<SkillsState>((set) => ({
  candidates: [],
  query: '',
  installing: {},
  lastInstalled: null,

  async search(client, wsId, q) {
    set({ query: q })
    const candidates = await discoverSkills(client, wsId, q)
    set({ candidates })
  },

  async install(client, wsId, candidateId) {
    set((s) => ({ installing: { ...s.installing, [candidateId]: true } }))
    try {
      const r = await installSkill(client, wsId, candidateId)
      set((s) => ({
        lastInstalled: { canonical_name: r.canonical_name, version: r.installed_version },
        installing: { ...s.installing, [candidateId]: false },
      }))
    } catch (e) {
      set((s) => ({ installing: { ...s.installing, [candidateId]: false } }))
      throw e
    }
  },

  async refresh(client, wsId, skillId) {
    const r = await refreshSkill(client, wsId, skillId)
    return r.changed
  },

  reset: () => set({ candidates: [], query: '', installing: {}, lastInstalled: null }),
}))
