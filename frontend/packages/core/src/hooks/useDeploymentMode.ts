'use client'

import useSWR from 'swr'

import { createApiClient } from '../api/client'
import { fetchSystemInfo, type SystemInfoResponse } from '../api/system'

export function useDeploymentMode() {
  const { data, error, isLoading } = useSWR<SystemInfoResponse>(
    '/api/v1/system/info',
    () => fetchSystemInfo(createApiClient('')),
    { revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false },
  )
  return {
    mode: data?.deployment_mode,
    version: data?.version,
    sandboxEnabled: data?.sandbox_enabled ?? false,
    passwordPolicy: data?.password_policy ?? 'high',
    loading: isLoading,
    error: error as Error | undefined,
  }
}
