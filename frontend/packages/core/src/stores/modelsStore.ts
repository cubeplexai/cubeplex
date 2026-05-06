import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { fetchProvider, createModel, updateModel, deleteModel, testModel } from '../api/providers'
import type { Model, ModelCreate, ModelUpdate, TestResult } from '../types/provider'

interface ModelsState {
  models: Model[]
  providerId: string | null
  loading: boolean
  error: string | null
  fetchModels: (client: ApiClient, providerId: string) => Promise<void>
  clearModels: () => void
  createModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>
  updateModel: (
    client: ApiClient,
    providerId: string,
    modelId: string,
    body: ModelUpdate,
  ) => Promise<void>
  deleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>
  testModel: (
    client: ApiClient,
    providerId: string,
    body: { model_id: string },
  ) => Promise<TestResult>
}

export const useModelsStore = create<ModelsState>((set) => ({
  models: [],
  providerId: null,
  loading: false,
  error: null,

  fetchModels: async (client, providerId) => {
    set({ loading: true, error: null, models: [], providerId })
    try {
      const provider = await fetchProvider(client, providerId)
      set((s) =>
        s.providerId === providerId ? { models: provider.models || [], loading: false } : s,
      )
    } catch (e) {
      set((s) =>
        s.providerId === providerId ? { error: (e as Error).message, loading: false } : s,
      )
    }
  },

  clearModels: () => set({ models: [], providerId: null, loading: false, error: null }),

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

  testModel: async (client, providerId, body) => {
    return testModel(client, providerId, body)
  },
}))
