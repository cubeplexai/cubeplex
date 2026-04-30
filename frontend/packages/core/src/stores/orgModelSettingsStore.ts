import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { fetchOrgLLMSettings, updateOrgLLMSettings } from '../api/providers'
import type { OrgLLMSettings, OrgLLMSettingsUpdate } from '../types/provider'

interface OrgModelSettingsState {
  settings: OrgLLMSettings | null
  loading: boolean
  error: string | null
  fetchSettings: (client: ApiClient) => Promise<void>
  updateSettings: (client: ApiClient, body: OrgLLMSettingsUpdate) => Promise<void>
}

export const useOrgModelSettingsStore = create<OrgModelSettingsState>((set) => ({
  settings: null,
  loading: false,
  error: null,

  fetchSettings: async (client) => {
    set({ loading: true, error: null })
    try {
      const settings = await fetchOrgLLMSettings(client)
      set({ settings, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  updateSettings: async (client, body) => {
    const settings = await updateOrgLLMSettings(client, body)
    set({ settings })
  },
}))
