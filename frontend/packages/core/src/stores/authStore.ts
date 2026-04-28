import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { getMe, type MeResult } from '../api/auth'

function writeLocaleCookie(locale: string): void {
  if (typeof document === 'undefined') return
  document.cookie = `NEXT_LOCALE=${locale}; path=/; SameSite=Lax`
}

function clearLocaleCookie(): void {
  if (typeof document === 'undefined') return
  document.cookie = 'NEXT_LOCALE=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax'
}

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
      if (user?.language) {
        writeLocaleCookie(user.language)
        client.setLocale(user.language)
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  reset() {
    clearLocaleCookie()
    set({ user: null, isLoading: false, error: null })
  },
}))
