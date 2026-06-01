/**
 * Sandbox policy + workspace sandbox status API helpers.
 *
 * Admin policy editor lives at /admin/sandbox-policy (org-scope); workspace
 * sandbox status lives under the workspace path. Mirrors the workspace-
 * settings.ts shape: types + thin client wrappers, no React.
 */

import { toApiError, type ApiClient } from './client'

export interface SandboxNetworkRule {
  action: 'allow' | 'deny'
  target: string
}

export interface SandboxCommandRule {
  action: 'allow' | 'deny' | 'confirm'
  pattern: string
}

export interface SandboxPolicyOut {
  default_image: string
  network_rules: SandboxNetworkRule[]
  command_rules: SandboxCommandRule[]
  network_default_action: 'allow' | 'deny'
  egress_proxy: string | null
  warnings: string[]
}

export interface UpdateSandboxPolicyIn {
  default_image: string
  network_rules: SandboxNetworkRule[] | null
  command_rules: SandboxCommandRule[] | null
  network_default_action: 'allow' | 'deny'
  egress_proxy: string | null
}

export type SandboxStatusValue = 'provisioning' | 'running' | 'paused' | 'terminated' | 'absent'

export interface SandboxStatusOut {
  status: SandboxStatusValue
  default_image: string | null
  last_activity_at: string | null
  browser_url: string | null
}

export async function getSandboxPolicy(client: ApiClient): Promise<SandboxPolicyOut> {
  const res = await client.get('/api/v1/admin/sandbox-policy')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SandboxPolicyOut
}

export async function putSandboxPolicy(
  client: ApiClient,
  body: UpdateSandboxPolicyIn,
): Promise<SandboxPolicyOut> {
  const res = await client.put('/api/v1/admin/sandbox-policy', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SandboxPolicyOut
}

export async function getWorkspaceSandboxStatus(
  client: ApiClient,
  wsId: string,
): Promise<SandboxStatusOut> {
  // Explicit /ws/{wsId}/ — bypass the ApiClient's workspaceId rewrite so this
  // helper works even when the client isn't pinned to the same workspace.
  const res = await client.get(`/api/v1/ws/${wsId}/sandbox/status`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SandboxStatusOut
}
