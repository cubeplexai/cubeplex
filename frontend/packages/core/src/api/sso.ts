/**
 * SSO + social-login API client.
 *
 * Routes:
 *   - Public (workspace-neutral): `/api/v1/auth/sso/*`, `/api/v1/auth/social/*`,
 *     `/api/v1/auth/org-info/*`. Neutrality is registered in `client.ts`.
 *   - Admin: `/api/v1/admin/sso/*`. The backend resolves `org_id` from the
 *     authenticated admin via `resolve_unambiguous_admin_org_id`, so the
 *     frontend never sends `org_id`.
 *
 * Timestamps come back as ISO 8601 strings (utc_isoformat) — kept as `string`,
 * not parsed to `Date`.
 */

import { toApiError, type ApiClient } from './client'

// --- Public (pre-login) shapes ---------------------------------------------

export interface OrgInfoResponse {
  org_name: string
  sso_enabled: boolean
  sso_protocol: string | null
}

export interface SsoInitiateRequest {
  org_slug?: string | null
}

export interface SsoInitiateResponse {
  redirect_url: string
}

export interface SocialAuthorizeResponse {
  redirect_url: string
}

// --- Admin shapes ----------------------------------------------------------

export type SsoProtocol = 'oidc' | 'saml'
export type SsoProvisioning = 'auto' | 'invite_only'
export type SsoStatus = 'testing' | 'active' | 'inactive'

export interface SsoConnectionResponse {
  id: string
  org_id: string
  protocol: string
  display_name: string
  status: string
  provisioning: string
  config: Record<string, unknown>
  last_idp_attributes: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface SsoValidateCheck {
  name: string
  passed: boolean
  detail: string
}

export interface SsoValidateResponse {
  checks: SsoValidateCheck[]
  all_passed: boolean
}

export interface SsoConnectionCreate {
  protocol: SsoProtocol
  display_name: string
  provisioning?: SsoProvisioning
  config: Record<string, unknown>
  client_secret?: string
}

export interface SsoConnectionUpdate {
  display_name?: string
  provisioning?: SsoProvisioning
  config?: Record<string, unknown>
}

export interface ExternalIdentityResponse {
  id: string
  user_id: string
  provider_type: string
  external_id: string
  external_email: string
  created_at: string
}

export interface OidcDiscoveryResponse {
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  userinfo_endpoint: string | null
  jwks_uri?: string | null
}

export interface ListSsoIdentitiesParams {
  limit?: number
  offset?: number
}

// --- Public endpoints ------------------------------------------------------

export async function getOrgInfo(client: ApiClient, orgSlug: string): Promise<OrgInfoResponse> {
  const res = await client.get(`/api/v1/auth/org-info/${encodeURIComponent(orgSlug)}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OrgInfoResponse
}

export async function initiateSsoLogin(
  client: ApiClient,
  orgSlug?: string,
): Promise<SsoInitiateResponse> {
  const body: SsoInitiateRequest = { org_slug: orgSlug ?? null }
  const res = await client.post('/api/v1/auth/sso/initiate', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoInitiateResponse
}

export async function getGoogleAuthorizeUrl(client: ApiClient): Promise<SocialAuthorizeResponse> {
  const res = await client.get('/api/v1/auth/social/google/authorize')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SocialAuthorizeResponse
}

// --- Admin endpoints -------------------------------------------------------

export async function getOrgSso(client: ApiClient): Promise<SsoConnectionResponse | null> {
  const res = await client.get('/api/v1/admin/sso')
  if (!res.ok) throw await toApiError(res)
  const text = await res.text()
  if (!text) return null
  const parsed = JSON.parse(text) as SsoConnectionResponse | null
  return parsed
}

export async function createSsoConnection(
  client: ApiClient,
  body: SsoConnectionCreate,
): Promise<SsoConnectionResponse> {
  const res = await client.post('/api/v1/admin/sso', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoConnectionResponse
}

export async function updateSsoConnection(
  client: ApiClient,
  ssoId: string,
  body: SsoConnectionUpdate,
): Promise<SsoConnectionResponse> {
  const res = await client.put(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}`, body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoConnectionResponse
}

export async function deleteSsoConnection(client: ApiClient, ssoId: string): Promise<void> {
  const res = await client.del(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}`)
  if (!res.ok) throw await toApiError(res)
}

export async function activateSsoConnection(
  client: ApiClient,
  ssoId: string,
): Promise<SsoConnectionResponse> {
  const res = await client.post(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}/activate`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoConnectionResponse
}

export async function deactivateSsoConnection(
  client: ApiClient,
  ssoId: string,
): Promise<SsoConnectionResponse> {
  const res = await client.post(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}/deactivate`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoConnectionResponse
}

export async function listSsoIdentities(
  client: ApiClient,
  ssoId: string,
  params?: ListSsoIdentitiesParams,
): Promise<ExternalIdentityResponse[]> {
  const qs = new URLSearchParams()
  if (params?.limit !== undefined) qs.set('limit', String(params.limit))
  if (params?.offset !== undefined) qs.set('offset', String(params.offset))
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  const res = await client.get(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}/identities${suffix}`)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as ExternalIdentityResponse[]
}

export async function unlinkSsoIdentity(
  client: ApiClient,
  ssoId: string,
  eid: string,
): Promise<void> {
  const res = await client.del(
    `/api/v1/admin/sso/${encodeURIComponent(ssoId)}/identities/${encodeURIComponent(eid)}`,
  )
  if (!res.ok) throw await toApiError(res)
}

export async function discoverOidcEndpoints(
  client: ApiClient,
  issuerUrl: string,
): Promise<OidcDiscoveryResponse> {
  const res = await client.post('/api/v1/admin/sso/discover-oidc', { issuer_url: issuerUrl })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OidcDiscoveryResponse
}

export async function validateSsoConnection(
  client: ApiClient,
  ssoId: string,
): Promise<SsoValidateResponse> {
  const res = await client.post(`/api/v1/admin/sso/${encodeURIComponent(ssoId)}/validate`, {})
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as SsoValidateResponse
}
