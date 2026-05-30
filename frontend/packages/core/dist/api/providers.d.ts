import type { ApiClient } from './client';
import type { Provider, ProviderCreate, ProviderUpdate, Model, ModelCreate, ModelUpdate, OrgLLMSettings, OrgLLMSettingsUpdate, VendorPreset, ProbeStep, ProbeResult } from '../types/provider';
export declare function fetchProviders(client: ApiClient): Promise<Provider[]>;
export declare function fetchProvider(client: ApiClient, id: string): Promise<Provider>;
export declare function createProvider(client: ApiClient, body: ProviderCreate): Promise<Provider>;
export declare function updateProvider(client: ApiClient, id: string, body: ProviderUpdate): Promise<Provider>;
export declare function deleteProvider(client: ApiClient, id: string): Promise<void>;
export declare function createModel(client: ApiClient, providerId: string, body: ModelCreate): Promise<Model>;
export declare function updateModel(client: ApiClient, providerId: string, modelId: string, body: ModelUpdate): Promise<Model>;
export declare function deleteModel(client: ApiClient, providerId: string, modelId: string): Promise<void>;
export declare function fetchOrgLLMSettings(client: ApiClient): Promise<OrgLLMSettings>;
export declare function updateOrgLLMSettings(client: ApiClient, body: OrgLLMSettingsUpdate): Promise<OrgLLMSettings>;
export declare function listPresets(client: ApiClient): Promise<VendorPreset[]>;
interface LivenessBody {
    api: string;
    base_url: string;
    api_key?: string | null;
    capability: Record<string, unknown>;
    model_capability_overrides?: Record<string, unknown>;
    model_id: string;
}
export declare function presaveLiveness(client: ApiClient, body: LivenessBody): Promise<ProbeStep>;
export declare function presaveTest(client: ApiClient, body: LivenessBody): Promise<ProbeResult>;
export declare function checkLiveness(client: ApiClient, providerId: string, modelId: string): Promise<ProbeStep>;
export declare function testModel(client: ApiClient, providerId: string, modelDbId: string): Promise<ProbeResult>;
export declare function setModelEnabled(client: ApiClient, providerId: string, modelDbId: string, enabled: boolean): Promise<Model>;
export {};
//# sourceMappingURL=providers.d.ts.map