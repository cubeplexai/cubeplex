'use client'

import useSWR from 'swr'

import { createApiClient } from '../api/client'
import { fetchSystemInfo, type SystemInfoResponse } from '../api/system'

/**
 * Backend-computed edition ('oss' | 'ee') + licensed feature flags.
 *
 * Shares the SWR key with useDeploymentMode, so it costs no extra request.
 * Defaults to 'oss' while loading — gate on `loading` wherever showing the OSS
 * surface first would flicker.
 */
export function useEdition() {
  const { data, isLoading } = useSWR<SystemInfoResponse>(
    '/api/v1/system/info',
    () => fetchSystemInfo(createApiClient('')),
    { revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false },
  )
  const features = data?.features ?? []
  return {
    edition: data?.edition ?? 'oss',
    features,
    hasFeature: (name: string) => features.includes(name),
    loading: isLoading,
  }
}
