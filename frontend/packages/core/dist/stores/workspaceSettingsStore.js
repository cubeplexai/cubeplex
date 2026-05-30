import { create } from 'zustand';
import { getAgentConfig, listWorkspaceMCPConnectors, listWorkspaceSkills, toggleWorkspaceSkill, updateAgentConfig, } from '../api/workspace-settings';
export const useWorkspaceSettingsStore = create((set, get) => ({
    agentConfig: null,
    skills: null,
    mcpEffectiveConnectors: null,
    loading: false,
    error: null,
    async loadAll(client) {
        set({ loading: true, error: null });
        try {
            const [agentConfig, skills] = await Promise.all([
                getAgentConfig(client),
                listWorkspaceSkills(client),
            ]);
            set({ agentConfig, skills, loading: false });
        }
        catch (e) {
            set({ loading: false, error: String(e) });
        }
    },
    async loadMcpEffectiveConnectors(client, wsId) {
        try {
            const items = await listWorkspaceMCPConnectors(client, wsId);
            set({ mcpEffectiveConnectors: items });
        }
        catch (e) {
            set({ error: String(e) });
        }
    },
    async savePersona(client, prompt) {
        const config = await updateAgentConfig(client, { system_prompt: prompt });
        set({ agentConfig: config });
    },
    async toggleSkill(client, installId, enabled) {
        await toggleWorkspaceSkill(client, installId, enabled);
        const skills = get().skills;
        if (!skills)
            return;
        const update = (list) => list.map((s) => (s.install_id === installId ? { ...s, enabled } : s));
        set({
            skills: {
                org_skills: update(skills.org_skills),
                workspace_skills: update(skills.workspace_skills),
            },
        });
    },
}));
//# sourceMappingURL=workspaceSettingsStore.js.map