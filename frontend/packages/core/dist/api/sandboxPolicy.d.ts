/**
 * Sandbox policy + workspace sandbox status API helpers.
 *
 * Admin policy editor lives at /admin/sandbox-policy (org-scope); workspace
 * sandbox status lives under the workspace path. Mirrors the workspace-
 * settings.ts shape: types + thin client wrappers, no React.
 */
import { type ApiClient } from './client';
export interface SandboxNetworkRule {
    action: 'allow' | 'deny';
    target: string;
}
export interface SandboxCommandRule {
    action: 'allow' | 'deny' | 'confirm';
    pattern: string;
}
export interface SandboxPolicyOut {
    default_image: string;
    network_rules: SandboxNetworkRule[];
    command_rules: SandboxCommandRule[];
    warnings: string[];
}
export interface UpdateSandboxPolicyIn {
    default_image: string;
    network_rules: SandboxNetworkRule[] | null;
    command_rules: SandboxCommandRule[] | null;
}
export type SandboxStatusValue = 'provisioning' | 'running' | 'paused' | 'terminated' | 'absent';
export interface SandboxStatusOut {
    status: SandboxStatusValue;
    default_image: string | null;
    last_activity_at: string | null;
    browser_url: string | null;
}
export declare function getSandboxPolicy(client: ApiClient): Promise<SandboxPolicyOut>;
export declare function putSandboxPolicy(client: ApiClient, body: UpdateSandboxPolicyIn): Promise<SandboxPolicyOut>;
export declare function getWorkspaceSandboxStatus(client: ApiClient, wsId: string): Promise<SandboxStatusOut>;
//# sourceMappingURL=sandboxPolicy.d.ts.map