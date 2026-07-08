import type { MCPConnector, MCPConnectorTemplate } from './mcp'

export type WsAvailableSource = 'org_install' | 'template'

export type WsAvailableReason = 'no_state_row' | 'state_disabled' | 'not_installed_at_org'

export interface WsAvailable {
  source: WsAvailableSource
  install: MCPConnector | null
  template: MCPConnectorTemplate | null
  reason: WsAvailableReason
}
