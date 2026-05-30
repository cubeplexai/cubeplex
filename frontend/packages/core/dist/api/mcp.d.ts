import type { MCPAuthMethod, MCPConnectorInstall, MCPConnectorTemplate, MCPCredentialGrantStatus, MCPEffectiveConnector, MCPOAuthStartResult, MCPTransport, MCPWorkspaceConnectorState } from '../types/mcp';
import type { AdminOrgConnector } from '../types/mcp_admin_connector';
import type { WsAvailable } from '../types/mcp_ws_available';
import { type ApiClient } from './client';
export declare function listTemplates(client: ApiClient): Promise<{
    items: MCPConnectorTemplate[];
}>;
export declare function wsListTemplates(client: ApiClient, wsId: string): Promise<{
    items: MCPConnectorTemplate[];
}>;
export declare function adminListTemplates(client: ApiClient): Promise<{
    items: MCPConnectorTemplate[];
}>;
export declare function adminCreateInstall(client: ApiClient, body: unknown): Promise<MCPConnectorInstall>;
export declare function adminGetInstall(client: ApiClient, installId: string): Promise<MCPConnectorInstall>;
export declare function adminPatchInstall(client: ApiClient, installId: string, body: unknown): Promise<MCPConnectorInstall>;
export declare function adminDeleteInstall(client: ApiClient, installId: string): Promise<void>;
export declare function wsCreateInstall(client: ApiClient, wsId: string, body: unknown): Promise<MCPConnectorInstall>;
export declare function wsDeleteInstall(client: ApiClient, wsId: string, installId: string): Promise<void>;
export declare function wsListEffectiveConnectors(client: ApiClient, wsId: string): Promise<{
    items: MCPEffectiveConnector[];
}>;
export declare function wsPatchConnectorState(client: ApiClient, wsId: string, installId: string, body: Partial<MCPWorkspaceConnectorState>): Promise<MCPWorkspaceConnectorState>;
export interface CreateGrantBody {
    credential_plaintext?: string;
    oauth_callback_state?: string;
    name?: string;
}
export declare function adminCreateOrgGrant(client: ApiClient, installId: string, body: CreateGrantBody): Promise<MCPCredentialGrantStatus>;
export declare function adminDeleteOrgGrant(client: ApiClient, installId: string): Promise<void>;
export declare function adminOrgGrantOAuthStart(client: ApiClient, installId: string): Promise<MCPOAuthStartResult>;
export declare function wsCreateWorkspaceGrant(client: ApiClient, wsId: string, installId: string, body: CreateGrantBody): Promise<MCPCredentialGrantStatus>;
export declare function wsDeleteWorkspaceGrant(client: ApiClient, wsId: string, installId: string): Promise<void>;
export declare function wsWorkspaceGrantOAuthStart(client: ApiClient, wsId: string, installId: string): Promise<MCPOAuthStartResult>;
export declare function wsCreateMyGrant(client: ApiClient, wsId: string, installId: string, body: CreateGrantBody): Promise<MCPCredentialGrantStatus>;
export declare function wsDeleteMyGrant(client: ApiClient, wsId: string, installId: string): Promise<void>;
export declare function wsMyGrantOAuthStart(client: ApiClient, wsId: string, installId: string): Promise<MCPOAuthStartResult>;
export interface MCPAdminInstallEffective {
    install_id: string;
    usable: boolean;
    reason: 'usable' | 'pending_oauth' | 'missing_org_grant' | 'grant_expired' | 'discovery_failed';
}
export declare function adminGetInstallEffective(client: ApiClient, installId: string): Promise<MCPAdminInstallEffective>;
export declare function adminRefreshDiscovery(client: ApiClient, installId: string, workspaceId?: string | null): Promise<MCPConnectorInstall>;
export declare function wsRefreshDiscovery(client: ApiClient, wsId: string, installId: string): Promise<MCPConnectorInstall>;
export interface ToolInvokeResult {
    ok: boolean;
    result?: unknown;
    error?: string | null;
    duration_ms: number;
}
export declare function adminInvokeTool(client: ApiClient, installId: string, toolName: string, args: Record<string, unknown>, workspaceId?: string | null): Promise<ToolInvokeResult>;
export declare function wsInvokeTool(client: ApiClient, wsId: string, installId: string, toolName: string, args: Record<string, unknown>): Promise<ToolInvokeResult>;
export interface TestConnectionBody {
    server_url: string;
    transport: MCPTransport;
    auth_method: MCPAuthMethod;
    credential_plaintext?: string | null;
    headers?: Record<string, string> | null;
}
export interface TestConnectionResult {
    ok: boolean;
    tool_count: number;
    error_code: string | null;
    error_message: string | null;
}
export declare function adminTestConnection(client: ApiClient, body: TestConnectionBody): Promise<TestConnectionResult>;
export interface PromoteDistribution {
    mode: 'all' | 'selected' | 'none';
    workspace_ids?: string[] | null;
}
export declare function adminPromoteToOrg(client: ApiClient, installId: string, distribution: PromoteDistribution): Promise<MCPConnectorInstall>;
export declare function adminUpsertToolCitation(client: ApiClient, installId: string, toolName: string, config: Record<string, unknown> | null): Promise<MCPConnectorInstall>;
export declare function adminListConnectors(client: ApiClient): Promise<{
    items: AdminOrgConnector[];
}>;
export declare function wsListAvailable(client: ApiClient, wsId: string): Promise<{
    items: WsAvailable[];
}>;
/** One MCP ``Icon`` (spec rev 2025-11-25). ``src`` is HTTP(S) URL or ``data:`` URI. */
export interface MCPToolIcon {
    src: string;
    mime_type: string | null;
    sizes: string[] | null;
    /** ``"light"`` / ``"dark"`` when the server supplies separate variants. */
    theme: string | null;
}
/** One MCP tool surfaced to the chat UI from a workspace's enabled installs. */
export interface MCPActiveTool {
    /** What the LLM sees and ``tool_call.name`` carries — used as the lookup key. */
    namespaced_name: string;
    /** Original (bare) tool name from the MCP server's tools/list. */
    bare_name: string;
    install_id: string;
    /** Install display name (the slug source for namespacing). */
    server_name: string;
    server_icons: MCPToolIcon[];
    tool_icons: MCPToolIcon[];
}
export declare function wsListActiveTools(client: ApiClient, wsId: string): Promise<{
    items: MCPActiveTool[];
}>;
//# sourceMappingURL=mcp.d.ts.map