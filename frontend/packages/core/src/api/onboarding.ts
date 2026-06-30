import { toApiError, type ApiClient } from './client'

export interface OnboardingRequest {
  org_name?: string
  org_slug?: string
  workspace_name: string
}

export interface OnboardingResponse {
  workspace_id: string
}

export async function completeOnboarding(
  client: ApiClient,
  body: OnboardingRequest,
): Promise<OnboardingResponse> {
  const res = await client.post('/api/v1/onboarding', body)
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as OnboardingResponse
}
