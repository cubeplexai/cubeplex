import { toApiError, type ApiClient } from './client'

export interface RegisterResult {
  id: string
  email: string
  default_workspace_id: string
}

export interface OrgMembership {
  org_id: string
  role: string
}

export interface MeResult {
  id: string
  email: string
  display_name: string | null
  avatar_url: string | null
  avatar_seed: string | null
  avatar_kind: string | null
  avatar_style: string | null
  language: string
  is_verified: boolean
  needs_org_setup?: boolean
  org_memberships?: OrgMembership[]
}

export async function registerUser(
  client: ApiClient,
  email: string,
  password: string,
  displayName?: string,
): Promise<RegisterResult> {
  const body: Record<string, string> = { email, password }
  if (displayName) body.display_name = displayName
  const res = await client.post('/api/v1/auth/register', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as RegisterResult
}

export async function loginUser(client: ApiClient, email: string, password: string): Promise<void> {
  const res = await client.postForm('/api/v1/auth/login', {
    username: email,
    password,
  })
  if (!res.ok) throw await toApiError(res)
}

export async function logoutUser(client: ApiClient): Promise<void> {
  const res = await client.post('/api/v1/auth/logout', {})
  if (!res.ok && res.status !== 401) throw await toApiError(res)
}

export async function getMe(client: ApiClient): Promise<MeResult | null> {
  const res = await client.get('/api/v1/auth/me')
  if (res.status === 401) return null
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}

export async function updateLanguage(client: ApiClient, language: 'en' | 'zh'): Promise<MeResult> {
  const res = await client.patch('/api/v1/auth/me', { language })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}

export async function updateProfile(
  client: ApiClient,
  patch: { display_name?: string; language?: 'en' | 'zh' },
): Promise<MeResult> {
  const res = await client.patch('/api/v1/auth/me', patch)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}

export async function verifyEmail(client: ApiClient, token: string): Promise<void> {
  const res = await client.post('/api/v1/auth/verify', { token })
  if (!res.ok) throw await toApiError(res)
}

export async function requestVerifyToken(client: ApiClient, email: string): Promise<void> {
  const res = await client.post('/api/v1/auth/request-verify-token', { email })
  if (!res.ok) throw await toApiError(res)
}

export interface UploadAvatarParams {
  file: File
  kind: 'uploaded' | 'generated'
  seed?: string
  style?: string
}

export async function uploadAvatar(
  client: ApiClient,
  params: UploadAvatarParams,
): Promise<MeResult> {
  const fd = new FormData()
  fd.append('file', params.file)
  fd.append('kind', params.kind)
  if (params.seed !== undefined) fd.append('seed', params.seed)
  if (params.style !== undefined) fd.append('style', params.style)
  const res = await client.put('/api/v1/auth/me/avatar', fd)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}

export async function deleteAvatar(client: ApiClient): Promise<MeResult> {
  const res = await client.del('/api/v1/auth/me/avatar')
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}
