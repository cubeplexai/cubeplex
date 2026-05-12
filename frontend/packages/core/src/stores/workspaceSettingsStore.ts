import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import {
  getAgentConfig,
  listWorkspaceMCP,
  listWorkspaceSkills,
  patchWorkspaceMCPCredentialMode,
  toggleWorkspaceMCP,
  toggleWorkspaceSkill,
  updateAgentConfig,
} from '../api/workspace-settings'
import type {
  AgentConfig,
  MCPCredentialMode,
  MCPServerItem,
  SkillInstall,
  WorkspaceMCP,
  WorkspaceSkills,
} from '../types/workspace-settings'

export interface WorkspaceSettingsStore {
  agentConfig: AgentConfig | null
  skills: WorkspaceSkills | null
  mcp: WorkspaceMCP | null
  loading: boolean
  error: string | null

  loadAll: (client: ApiClient) => Promise<void>
  savePersona: (client: ApiClient, prompt: string) => Promise<void>
  toggleSkill: (client: ApiClient, installId: string, enabled: boolean) => Promise<void>
  toggleMCP: (client: ApiClient, serverId: string, enabled: boolean) => Promise<void>
  patchMCPCredentialMode: (
    client: ApiClient,
    serverId: string,
    mode: MCPCredentialMode,
  ) => Promise<void>
}

export const useWorkspaceSettingsStore = create<WorkspaceSettingsStore>((set, get) => ({
  agentConfig: null,
  skills: null,
  mcp: null,
  loading: false,
  error: null,

  async loadAll(client: ApiClient) {
    set({ loading: true, error: null })
    try {
      const [agentConfig, skills, mcp] = await Promise.all([
        getAgentConfig(client),
        listWorkspaceSkills(client),
        listWorkspaceMCP(client),
      ])
      set({ agentConfig, skills, mcp, loading: false })
    } catch (e) {
      set({ loading: false, error: String(e) })
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

  async toggleMCP(client: ApiClient, serverId: string, enabled: boolean) {
    await toggleWorkspaceMCP(client, serverId, enabled)
    const mcp = get().mcp
    if (!mcp) return
    const update = (list: MCPServerItem[]) =>
      list.map((s) => (s.server_id === serverId ? { ...s, enabled } : s))
    set({
      mcp: {
        org_servers: update(mcp.org_servers),
        workspace_servers: update(mcp.workspace_servers),
      },
    })
  },

  async patchMCPCredentialMode(client: ApiClient, serverId: string, mode: MCPCredentialMode) {
    await patchWorkspaceMCPCredentialMode(client, serverId, mode)
    const mcp = get().mcp
    if (!mcp) return
    const update = (list: MCPServerItem[]) =>
      list.map((s) =>
        s.server_id === serverId
          ? { ...s, credential_mode: mode, credential_source: 'needs_setup' as const }
          : s,
      )
    set({
      mcp: {
        org_servers: update(mcp.org_servers),
        workspace_servers: update(mcp.workspace_servers),
      },
    })
  },
}))
