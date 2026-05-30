import type { ApiClient } from '../api/client';
import { type MeResult } from '../api/auth';
export interface AuthStore {
    user: MeResult | null;
    isLoading: boolean;
    error: string | null;
    loadMe(client: ApiClient): Promise<void>;
    reset(): void;
}
export declare const useAuthStore: import("zustand").UseBoundStore<import("zustand").StoreApi<AuthStore>>;
//# sourceMappingURL=authStore.d.ts.map