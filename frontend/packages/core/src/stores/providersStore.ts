import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  fetchProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  testConnection,
} from '../api/providers'
import type { Provider, ProviderCreate, ProviderUpdate, TestResult } from '../types/provider'

interface ProvidersState {
  providers: Provider[]
  selectedId: string | null
  loading: boolean
  error: string | null
  fetchProviders: (client: ApiClient) => Promise<void>
  selectProvider: (id: string | null) => void
  createProvider: (client: ApiClient, body: ProviderCreate) => Promise<Provider>
  updateProvider: (client: ApiClient, id: string, body: ProviderUpdate) => Promise<void>
  deleteProvider: (client: ApiClient, id: string) => Promise<void>
  testConnection: (
    client: ApiClient,
    body: { provider_type: string; base_url: string; api_key?: string | null; auth_type: string },
  ) => Promise<TestResult>
}

export const useProvidersStore = create<ProvidersState>((set, _get) => ({
  providers: [],
  selectedId: null,
  loading: false,
  error: null,

  fetchProviders: async (client) => {
    set({ loading: true, error: null })
    try {
      const providers = await fetchProviders(client)
      set({ providers, loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  selectProvider: (id) => set({ selectedId: id }),

  createProvider: async (client, body) => {
    const provider = await createProvider(client, body)
    set((s) => ({ providers: [...s.providers, provider] }))
    return provider
  },

  updateProvider: async (client, id, body) => {
    const updated = await updateProvider(client, id, body)
    set((s) => ({
      providers: s.providers.map((p) => (p.id === id ? updated : p)),
    }))
  },

  deleteProvider: async (client, id) => {
    await deleteProvider(client, id)
    set((s) => ({
      providers: s.providers.filter((p) => p.id !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
    }))
  },

  testConnection: async (client, body) => {
    return testConnection(client, body)
  },
}))
