import type { ApiClient } from '../api/client';
import type { Model, ModelCreate, ModelUpdate } from '../types/provider';
interface ModelsState {
    models: Model[];
    providerId: string | null;
    loading: boolean;
    error: string | null;
    fetchModels: (client: ApiClient, providerId: string) => Promise<void>;
    clearModels: () => void;
    createModel: (client: ApiClient, providerId: string, body: ModelCreate) => Promise<Model>;
    updateModel: (client: ApiClient, providerId: string, modelId: string, body: ModelUpdate) => Promise<void>;
    deleteModel: (client: ApiClient, providerId: string, modelId: string) => Promise<void>;
}
export declare const useModelsStore: import("zustand").UseBoundStore<import("zustand").StoreApi<ModelsState>>;
export {};
//# sourceMappingURL=modelsStore.d.ts.map