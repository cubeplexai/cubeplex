import type { ApiClient } from '../api/client';
import type { OrgLLMSettings, OrgLLMSettingsUpdate } from '../types/provider';
interface OrgModelSettingsState {
    settings: OrgLLMSettings | null;
    loading: boolean;
    error: string | null;
    fetchSettings: (client: ApiClient) => Promise<void>;
    updateSettings: (client: ApiClient, body: OrgLLMSettingsUpdate) => Promise<void>;
}
export declare const useOrgModelSettingsStore: import("zustand").UseBoundStore<import("zustand").StoreApi<OrgModelSettingsState>>;
export {};
//# sourceMappingURL=orgModelSettingsStore.d.ts.map