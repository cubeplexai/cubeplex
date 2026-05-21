'use client'

import type { ApiClient, ProviderPreset } from '@cubebox/core'

interface ConfigureStepProps {
  client: ApiClient
  preset: ProviderPreset
  onProviderCreated: (providerId: string) => void
}

export function ConfigureStep(_props: ConfigureStepProps) {
  return null
}
