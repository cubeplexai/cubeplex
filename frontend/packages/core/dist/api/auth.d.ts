import { type ApiClient } from './client';
export interface RegisterResult {
    id: string;
    email: string;
    default_workspace_id: string;
}
export interface OrgMembership {
    org_id: string;
    role: string;
}
export interface MeResult {
    id: string;
    email: string;
    language: string;
    needs_org_setup?: boolean;
    org_memberships?: OrgMembership[];
}
export declare function registerUser(client: ApiClient, email: string, password: string): Promise<RegisterResult>;
export declare function loginUser(client: ApiClient, email: string, password: string): Promise<void>;
export declare function logoutUser(client: ApiClient): Promise<void>;
export declare function getMe(client: ApiClient): Promise<MeResult | null>;
export declare function updateLanguage(client: ApiClient, language: 'en' | 'zh'): Promise<MeResult>;
//# sourceMappingURL=auth.d.ts.map