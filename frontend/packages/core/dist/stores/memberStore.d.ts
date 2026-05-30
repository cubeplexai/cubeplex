import type { ApiClient } from '../api/client';
import { type OrgMember, type WsMember, type AvailableMember } from '../api/members';
export interface MemberStore {
    orgMembers: OrgMember[];
    orgLoading: boolean;
    wsMembers: WsMember[];
    wsLoading: boolean;
    available: AvailableMember[];
    loadOrgMembers(client: ApiClient): Promise<void>;
    addOrgMember(client: ApiClient, email: string, role: string): Promise<void>;
    updateOrgMemberRole(client: ApiClient, userId: string, role: string): Promise<void>;
    removeOrgMember(client: ApiClient, userId: string): Promise<void>;
    loadWsMembers(client: ApiClient, wsId: string): Promise<void>;
    loadAvailable(client: ApiClient, wsId: string): Promise<void>;
    addWsMember(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>;
    updateWsMemberRole(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>;
    removeWsMember(client: ApiClient, wsId: string, userId: string): Promise<void>;
    reset(): void;
}
export declare const useMemberStore: import("zustand").UseBoundStore<import("zustand").StoreApi<MemberStore>>;
//# sourceMappingURL=memberStore.d.ts.map