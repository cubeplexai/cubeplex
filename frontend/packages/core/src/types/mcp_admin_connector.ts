import type { MCPConnector, MCPConnectorTemplate } from './mcp'

export type AdminOrgReason =
  'usable' | 'missing_org_grant' | 'pending_oauth' | 'grant_expired' | 'discovery_failed'

export type AdminOrgCredentialAvailability = 'available' | 'missing' | 'not_required'

export interface AdminOrgEffective {
  usable: boolean
  reason: AdminOrgReason
  credential_availability: AdminOrgCredentialAvailability | null
}

export interface WorkspaceDistribution {
  enabled_count: number
  disabled_count: number
  eligible_count: number
  auto_enroll_new_workspaces: boolean
}

export interface AdminOrgConnector {
  install: MCPConnector
  template: MCPConnectorTemplate | null
  org_effective: AdminOrgEffective
  workspace_distribution: WorkspaceDistribution
}
