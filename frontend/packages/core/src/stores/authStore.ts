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

// Different routes/layouts (e.g. the onboarding page and the app shell it
// navigates into) each call loadMe() on mount. Their requests can resolve
// out of order, so a stale response must not clobber a fresher one — track
// the latest request and drop results from any call that's been superseded.
let latestRequestId = 0

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  isLoading: false,
  error: null,

  async loadMe(client) {
    const requestId = ++latestRequestId
    set({ isLoading: true, error: null })
    try {
      const user = await getMe(client)
      if (requestId !== latestRequestId) return
      set({ user })
      if (user?.language) {
        writeLocaleCookie(user.language)
        client.setLocale(user.language)
      }
    } catch (err) {
      if (requestId !== latestRequestId) return
      set({ error: (err as Error).message })
    } finally {
      if (requestId === latestRequestId) set({ isLoading: false })
    }
  },

  reset() {
    latestRequestId++
    clearLocaleCookie()
    set({ user: null, isLoading: false, error: null })
  },
}))
