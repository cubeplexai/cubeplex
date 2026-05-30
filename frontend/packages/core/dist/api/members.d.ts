import { type ApiClient } from './client';
export interface OrgMember {
    user_id: string;
    email: string;
    role: 'owner' | 'admin' | 'member';
    created_at: string;
}
export interface WsMember {
    user_id: string;
    email: string;
    role: 'admin' | 'member';
    created_at: string;
}
export interface AvailableMember {
    user_id: string;
    email: string;
    org_role: string;
}
export declare function listOrgMembers(client: ApiClient): Promise<OrgMember[]>;
export declare function addOrgMember(client: ApiClient, email: string, role: string): Promise<{
    user_id: string;
    email: string;
    role: string;
}>;
export declare function updateOrgMemberRole(client: ApiClient, userId: string, role: string): Promise<void>;
export declare function removeOrgMember(client: ApiClient, userId: string): Promise<void>;
export declare function listWsMembers(client: ApiClient, wsId: string): Promise<WsMember[]>;
export declare function listAvailableMembers(client: ApiClient, wsId: string): Promise<AvailableMember[]>;
export declare function addWsMember(client: ApiClient, wsId: string, userId: string, role: string): Promise<{
    user_id: string;
    email: string;
    role: string;
}>;
export declare function updateWsMemberRole(client: ApiClient, wsId: string, userId: string, role: string): Promise<void>;
export declare function removeWsMember(client: ApiClient, wsId: string, userId: string): Promise<void>;
//# sourceMappingURL=members.d.ts.map