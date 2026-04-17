import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { getMe, type MeResult } from '../api/auth'

export interface AuthStore {
  user: MeResult | null
  isLoading: boolean
  error: string | null
  loadMe(client: ApiClient): Promise<void>
  reset(): void
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  isLoading: false,
  error: null,

  async loadMe(client) {
    set({ isLoading: true, error: null })
    try {
      const user = await getMe(client)
      set({ user })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  reset() {
    set({ user: null, isLoading: false, error: null })
  },
}))
