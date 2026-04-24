'use client'

import useSWR from 'swr'

type AdminMeResponse = {
  is_admin: boolean
  org_id: string
  org_name: string
}

async function fetcher(url: string): Promise<AdminMeResponse> {
  const res = await fetch(url, { credentials: 'include' })
  if (res.status === 401) {
    throw new Error('unauthorized')
  }
  if (!res.ok) {
    throw new Error(`admin/me failed: ${res.status}`)
  }
  return res.json() as Promise<AdminMeResponse>
}

export function useAdminAccess() {
  const { data, error, isLoading } = useSWR<AdminMeResponse>('/api/v1/admin/me', fetcher, {
    revalidateOnFocus: false,
    shouldRetryOnError: false,
  })
  return {
    isAdmin: data?.is_admin ?? false,
    orgId: data?.org_id ?? null,
    orgName: data?.org_name ?? '',
    loading: isLoading,
    error: error as Error | undefined,
  }
}
