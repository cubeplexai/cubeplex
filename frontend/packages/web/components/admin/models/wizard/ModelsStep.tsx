'use client'

import type { ApiClient, ProviderPreset } from '@cubebox/core'

interface ModelsStepProps {
  client: ApiClient
  preset: ProviderPreset
  providerId: string
  onModelsCreated: (modelDbIds: string[]) => void
}

export function ModelsStep(_props: ModelsStepProps) {
  return null
}
