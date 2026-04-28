import { toApiError, type ApiClient } from './client'

export interface RegisterResult {
  id: string
  email: string
  default_workspace_id: string
}

export interface MeResult {
  id: string
  email: string
  language: string
}

export async function registerUser(
  client: ApiClient,
  email: string,
  password: string,
): Promise<RegisterResult> {
  const res = await client.post('/api/v1/auth/register', { email, password })
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

export async function updateLanguage(client: ApiClient, language: string): Promise<MeResult> {
  const res = await client.patch('/api/v1/auth/me', { language })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as MeResult
}
