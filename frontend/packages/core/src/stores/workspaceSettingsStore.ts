import { create } from 'zustand'

import type { ApiClient } from '../api/client'
import {
  getAgentConfig,
  listWorkspaceMCPConnectors,
  listWorkspaceSkills,
  toggleWorkspaceSkill,
  updateAgentConfig,
} from '../api/workspace-settings'
import type { MCPEffectiveConnector } from '../types/mcp'
import type { AgentConfig, SkillInstall, WorkspaceSkills } from '../types/workspace-settings'

export interface WorkspaceSettingsStore {
  agentConfig: AgentConfig | null
  skills: WorkspaceSkills | null
  /**
   * Four-layer effective connector list for the currently-loaded workspace.
   * Populated lazily by `loadMcpEffectiveConnectors`.
   */
  mcpEffectiveConnectors: MCPEffectiveConnector[] | null
  loading: boolean
  error: string | null

  loadAll: (client: ApiClient) => Promise<void>
  loadMcpEffectiveConnectors: (client: ApiClient, wsId: string) => Promise<void>
  savePersona: (client: ApiClient, prompt: string) => Promise<void>
  toggleSkill: (client: ApiClient, installId: string, enabled: boolean) => Promise<void>
}

export const useWorkspaceSettingsStore = create<WorkspaceSettingsStore>((set, get) => ({
  agentConfig: null,
  skills: null,
  mcpEffectiveConnectors: null,
  loading: false,
  error: null,

  async loadAll(client: ApiClient) {
    set({ loading: true, error: null })
    try {
      const [agentConfig, skills] = await Promise.all([
        getAgentConfig(client),
        listWorkspaceSkills(client),
      ])
      set({ agentConfig, skills, loading: false })
    } catch (e) {
      set({ loading: false, error: String(e) })
    }
  },

  async loadMcpEffectiveConnectors(client: ApiClient, wsId: string) {
    try {
      const items = await listWorkspaceMCPConnectors(client, wsId)
      set({ mcpEffectiveConnectors: items })
    } catch (e) {
      set({ error: String(e) })
    }
  },

  async savePersona(client: ApiClient, prompt: string) {
    const config = await updateAgentConfig(client, { system_prompt: prompt })
    set({ agentConfig: config })
  },

  async toggleSkill(client: ApiClient, installId: string, enabled: boolean) {
    await toggleWorkspaceSkill(client, installId, enabled)
    const skills = get().skills
    if (!skills) return
    const update = (list: SkillInstall[]) =>
      list.map((s) => (s.install_id === installId ? { ...s, enabled } : s))
    set({
      skills: {
        org_skills: update(skills.org_skills),
        workspace_skills: update(skills.workspace_skills),
      },
    })
  },
}))
