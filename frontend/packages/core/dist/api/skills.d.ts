import { type ApiClient } from './client';
export interface SkillCandidateOut {
    candidate_id: string;
    name: string;
    canonical_name: string;
    description: string;
    source_kind: 'local' | 'remote';
    keywords: string[];
    version: string | null;
    trust: 'official' | 'community' | 'untrusted';
    install_state: 'enabled' | 'in_catalog' | 'available';
    stars: number | null;
    install_count: number | null;
    source_name: string;
    repo: string | null;
    unvetted: boolean;
}
export type SkillCandidateListResponse = SkillCandidateOut[];
export interface SkillInstallResponse {
    canonical_name: string;
    skill_id: string;
    installed_version: string;
}
export interface SkillRefreshResponse {
    canonical_name: string;
    skill_id: string;
    installed_version: string;
    /** False when re-import produced no new version. */
    changed: boolean;
}
export declare function discoverSkills(client: ApiClient, wsId: string, q: string, limit?: number): Promise<SkillCandidateListResponse>;
export declare function installSkill(client: ApiClient, wsId: string, candidateId: string): Promise<SkillInstallResponse>;
export declare function refreshSkill(client: ApiClient, wsId: string, skillId: string): Promise<SkillRefreshResponse>;
//# sourceMappingURL=skills.d.ts.map