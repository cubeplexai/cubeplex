import type { ApiClient } from '../api/client';
import { type SkillCandidateOut } from '../api/skills';
export interface SkillsState {
    candidates: SkillCandidateOut[];
    query: string;
    installing: Record<string, boolean>;
    lastInstalled: {
        canonical_name: string;
        version: string;
    } | null;
    search: (client: ApiClient, wsId: string, q: string) => Promise<void>;
    install: (client: ApiClient, wsId: string, candidateId: string) => Promise<void>;
    refresh: (client: ApiClient, wsId: string, skillId: string) => Promise<boolean>;
    reset: () => void;
}
export declare const useSkillsStore: import("zustand").UseBoundStore<import("zustand").StoreApi<SkillsState>>;
//# sourceMappingURL=skillsStore.d.ts.map