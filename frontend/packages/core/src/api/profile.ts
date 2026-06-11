import { toApiError, type ApiClient } from './client'

export async function forgotPassword(client: ApiClient, email: string): Promise<void> {
  const res = await client.post('/api/v1/auth/forgot-password', { email })
  // fastapi-users returns 202 regardless of whether the email exists
  if (!res.ok && res.status !== 202) throw await toApiError(res)
}

export async function resetPassword(
  client: ApiClient,
  token: string,
  password: string,
): Promise<void> {
  const res = await client.post('/api/v1/auth/reset-password', { token, password })
  if (!res.ok) throw await toApiError(res)
}

export async function changePassword(
  client: ApiClient,
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const res = await client.post('/api/v1/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  if (!res.ok) throw await toApiError(res)
}
