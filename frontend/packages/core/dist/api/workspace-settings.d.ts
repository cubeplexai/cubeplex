import type { MCPEffectiveConnector } from '../types/mcp';
import type { AgentConfig, WorkspaceSkills } from '../types/workspace-settings';
import { type ApiClient } from './client';
export declare function getAgentConfig(client: ApiClient): Promise<AgentConfig>;
export declare function updateAgentConfig(client: ApiClient, patch: Partial<AgentConfig>): Promise<AgentConfig>;
export declare function listWorkspaceSkills(client: ApiClient): Promise<WorkspaceSkills>;
export declare function toggleWorkspaceSkill(client: ApiClient, installId: string, enabled: boolean): Promise<{
    install_id: string;
    enabled: boolean;
}>;
export declare function installWorkspaceSkill(client: ApiClient, skillId: string, version: string): Promise<{
    install_id: string;
    skill_id: string;
    scope: string;
}>;
export declare function deleteWorkspaceSkill(client: ApiClient, installId: string): Promise<void>;
/**
 * Four-layer effective connector list for a workspace.
 */
export declare function listWorkspaceMCPConnectors(client: ApiClient, wsId: string): Promise<MCPEffectiveConnector[]>;
//# sourceMappingURL=workspace-settings.d.ts.map