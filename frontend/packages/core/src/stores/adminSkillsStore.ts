import { create } from 'zustand'

import { adminDiscoverSkills, adminInstallCandidate } from '../api/adminSkills'
import type { SkillCandidateOut } from '../api/skills'

export interface AdminSkillsState {
  candidates: SkillCandidateOut[]
  query: string
  searching: boolean
  installing: Record<string, boolean>
  lastInstalled: string | null
  search: (q: string) => Promise<void>
  install: (candidateId: string) => Promise<void>
  reset: () => void
}

export const useAdminSkillsStore = create<AdminSkillsState>((set) => ({
  candidates: [],
  query: '',
  searching: false,
  installing: {},
  lastInstalled: null,

  async search(q) {
    set({ query: q, candidates: [], searching: true })
    try {
      const candidates = await adminDiscoverSkills(q)
      set({ candidates, searching: false })
    } catch {
      set({ candidates: [], searching: false })
    }
  },

  async install(candidateId) {
    set((s) => ({ installing: { ...s.installing, [candidateId]: true } }))
    try {
      const r = await adminInstallCandidate(candidateId)
      set((s) => ({
        lastInstalled: r.skill_id,
        installing: { ...s.installing, [candidateId]: false },
      }))
    } catch (e) {
      set((s) => ({ installing: { ...s.installing, [candidateId]: false } }))
      throw e
    }
  },

  reset: () =>
    set({ candidates: [], query: '', searching: false, installing: {}, lastInstalled: null }),
}))
