import { toApiError, type ApiClient } from './client'

// ── Types (mirror backend ImRuntimeStatus + IMAccountOut) ────────────────────

export type ImConnectionState = 'connected' | 'disconnected' | 'never_connected'

export interface ImRuntimeStatus {
  connection_state: ImConnectionState
  last_inbound_at: string | null
  bot_open_id: string | null
  pending_queue: number
  matched_24h: number
  rejected_24h: number
}

export interface ImAccount {
  id: string
  platform: 'feishu' | string
  external_account_id: string
  workspace_id: string
  acting_user_id: string
  delivery_mode: 'long_connection' | 'webhook' | 'gateway'
  enabled: boolean
  runtime: ImRuntimeStatus
  bot_app_name: string | null
  bot_avatar_url: string | null
}

export interface ImAccountListOut {
  accounts: ImAccount[]
}

export interface ConnectFeishuAccountIn {
  platform: 'feishu'
  app_id: string
  app_secret: string
  encrypt_key?: string
  verification_token?: string
  domain?: 'feishu' | 'lark'
  delivery_mode?: 'long_connection' | 'webhook'
  acting_user_id?: string
}

export interface ConnectDiscordAccountIn {
  platform: 'discord'
  bot_token: string
  application_id: string
  acting_user_id?: string
}

export type ConnectImAccountIn = ConnectFeishuAccountIn | ConnectDiscordAccountIn

// ── Workspace scope ──────────────────────────────────────────────────────────

export async function wsListImAccounts(client: ApiClient, wsId: string): Promise<ImAccountListOut> {
  const res = await client.get(`/api/v1/ws/${wsId}/im/accounts`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccountListOut
}

export async function wsConnectImAccount(
  client: ApiClient,
  wsId: string,
  body: ConnectImAccountIn,
): Promise<ImAccount> {
  const res = await client.post(`/api/v1/ws/${wsId}/im/accounts`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccount
}

export async function wsDeleteImAccount(
  client: ApiClient,
  wsId: string,
  accountId: string,
): Promise<void> {
  const res = await client.del(`/api/v1/ws/${wsId}/im/accounts/${accountId}`)
  if (!res.ok) throw await toApiError(res)
}

export async function wsDisableImAccount(
  client: ApiClient,
  wsId: string,
  accountId: string,
): Promise<ImAccount> {
  const res = await client.post(`/api/v1/ws/${wsId}/im/accounts/${accountId}/disable`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccount
}

export async function wsEnableImAccount(
  client: ApiClient,
  wsId: string,
  accountId: string,
): Promise<ImAccount> {
  const res = await client.post(`/api/v1/ws/${wsId}/im/accounts/${accountId}/enable`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccount
}

// ── Admin scope ──────────────────────────────────────────────────────────────

export async function adminListImAccounts(client: ApiClient): Promise<ImAccountListOut> {
  const res = await client.get('/api/v1/admin/im/accounts')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccountListOut
}

export async function adminDisableImAccount(
  client: ApiClient,
  accountId: string,
): Promise<ImAccount> {
  const res = await client.post(`/api/v1/admin/im/accounts/${accountId}/disable`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccount
}

export async function adminEnableImAccount(
  client: ApiClient,
  accountId: string,
): Promise<ImAccount> {
  const res = await client.post(`/api/v1/admin/im/accounts/${accountId}/enable`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ImAccount
}
