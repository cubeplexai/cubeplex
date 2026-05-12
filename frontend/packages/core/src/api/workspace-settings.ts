import { toApiError, type ApiClient } from './client'
import type {
  AgentConfig,
  MCPCredentialMode,
  WorkspaceMCP,
  WorkspaceSkills,
} from '../types/workspace-settings'

export async function getAgentConfig(client: ApiClient): Promise<AgentConfig> {
  const res = await client.get('/api/v1/settings/agent')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AgentConfig
}

export async function updateAgentConfig(
  client: ApiClient,
  patch: Partial<AgentConfig>,
): Promise<AgentConfig> {
  const res = await client.put('/api/v1/settings/agent', patch)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as AgentConfig
}

export async function listWorkspaceSkills(client: ApiClient): Promise<WorkspaceSkills> {
  const res = await client.get('/api/v1/settings/skills')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WorkspaceSkills
}

export async function toggleWorkspaceSkill(
  client: ApiClient,
  installId: string,
  enabled: boolean,
): Promise<{ install_id: string; enabled: boolean }> {
  const res = await client.patch(`/api/v1/settings/skills/${installId}`, { enabled })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { install_id: string; enabled: boolean }
}

export async function installWorkspaceSkill(
  client: ApiClient,
  skillId: string,
  version: string,
): Promise<{ install_id: string; skill_id: string; scope: string }> {
  const res = await client.post('/api/v1/settings/skills', { skill_id: skillId, version })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { install_id: string; skill_id: string; scope: string }
}

export async function deleteWorkspaceSkill(client: ApiClient, installId: string): Promise<void> {
  const res = await client.del(`/api/v1/settings/skills/${installId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function listWorkspaceMCP(client: ApiClient): Promise<WorkspaceMCP> {
  const res = await client.get('/api/v1/settings/mcp')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as WorkspaceMCP
}

export async function toggleWorkspaceMCP(
  client: ApiClient,
  serverId: string,
  enabled: boolean,
): Promise<{ server_id: string; enabled: boolean }> {
  const res = await client.patch(`/api/v1/settings/mcp/${serverId}`, { enabled })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as { server_id: string; enabled: boolean }
}

export async function patchWorkspaceMCPCredentialMode(
  client: ApiClient,
  serverId: string,
  credentialMode: MCPCredentialMode,
): Promise<{ server_id: string; credential_mode: MCPCredentialMode }> {
  const res = await client.patch(`/api/v1/settings/mcp/${serverId}`, {
    credential_mode: credentialMode,
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as {
    server_id: string
    credential_mode: MCPCredentialMode
  }
}
