import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { fetchProvider, createModel, updateModel, deleteModel } from '../api/providers'
import type { Model, ModelCreate, ModelUpdate } from '../types/provider'

interface ModelsState {
  models: Model[]
  loading: boolean
  error: string | null
  fetchModels: (client: ApiClient, providerId: string) => Promise<void>
  createModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>
  updateModel: (
    client: ApiClient,
    providerId: string,
    modelId: string,
    body: ModelUpdate,
  ) => Promise<void>
  deleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>
}

export const useModelsStore = create<ModelsState>((set) => ({
  models: [],
  loading: false,
  error: null,

  fetchModels: async (client, providerId) => {
    set({ loading: true, error: null })
    try {
      const provider = await fetchProvider(client, providerId)
      set({ models: provider.models || [], loading: false })
    } catch (e) {
      set({ error: (e as Error).message, loading: false })
    }
  },

  createModel: async (client, providerId, body) => {
    const model = await createModel(client, providerId, body)
    set((s) => ({ models: [...s.models, model] }))
    return model
  },

  updateModel: async (client, providerId, modelId, body) => {
    const updated = await updateModel(client, providerId, modelId, body)
    set((s) => ({
      models: s.models.map((m) => (m.id === modelId ? updated : m)),
    }))
  },

  deleteModel: async (client, providerId, modelId) => {
    await deleteModel(client, providerId, modelId)
    set((s) => ({ models: s.models.filter((m) => m.id !== modelId) }))
  },
}))
