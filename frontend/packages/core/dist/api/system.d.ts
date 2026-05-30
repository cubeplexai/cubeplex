import { type ApiClient } from './client';
export interface SystemInfoResponse {
    deployment_mode: 'single_tenant' | 'multi_tenant';
    version: string;
    needs_org_setup: boolean;
    sandbox_enabled?: boolean;
}
export interface SetupRequest {
    org_name: string;
    slug: string;
}
export interface SetupResponse {
    org_id: string;
    workspace_id: string;
}
export declare function fetchSystemInfo(client: ApiClient): Promise<SystemInfoResponse>;
export declare function postSetup(client: ApiClient, body: SetupRequest): Promise<SetupResponse>;
//# sourceMappingURL=system.d.ts.map