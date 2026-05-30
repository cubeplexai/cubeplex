import type { ApiClient } from '../api/client';
import type { MCPEffectiveConnector } from '../types/mcp';
import type { AgentConfig, WorkspaceSkills } from '../types/workspace-settings';
export interface WorkspaceSettingsStore {
    agentConfig: AgentConfig | null;
    skills: WorkspaceSkills | null;
    /**
     * Four-layer effective connector list for the currently-loaded workspace.
     * Populated lazily by `loadMcpEffectiveConnectors`.
     */
    mcpEffectiveConnectors: MCPEffectiveConnector[] | null;
    loading: boolean;
    error: string | null;
    loadAll: (client: ApiClient) => Promise<void>;
    loadMcpEffectiveConnectors: (client: ApiClient, wsId: string) => Promise<void>;
    savePersona: (client: ApiClient, prompt: string) => Promise<void>;
    toggleSkill: (client: ApiClient, installId: string, enabled: boolean) => Promise<void>;
}
export declare const useWorkspaceSettingsStore: import("zustand").UseBoundStore<import("zustand").StoreApi<WorkspaceSettingsStore>>;
//# sourceMappingURL=workspaceSettingsStore.d.ts.map