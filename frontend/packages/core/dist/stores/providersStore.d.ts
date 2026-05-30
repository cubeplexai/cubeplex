import type { ApiClient } from '../api/client';
import type { Provider, ProviderCreate, ProviderUpdate } from '../types/provider';
interface ProvidersState {
    providers: Provider[];
    selectedId: string | null;
    loading: boolean;
    error: string | null;
    fetchProviders: (client: ApiClient) => Promise<void>;
    selectProvider: (id: string | null) => void;
    createProvider: (client: ApiClient, body: ProviderCreate) => Promise<Provider>;
    updateProvider: (client: ApiClient, id: string, body: ProviderUpdate) => Promise<void>;
    deleteProvider: (client: ApiClient, id: string) => Promise<void>;
}
export declare const useProvidersStore: import("zustand").UseBoundStore<import("zustand").StoreApi<ProvidersState>>;
export {};
//# sourceMappingURL=providersStore.d.ts.map